from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from .models import PasskeyCredential

User = get_user_model()


class UserRegistrationSerializer(serializers.ModelSerializer):
    """Serializer for user registration"""
    password = serializers.CharField(write_only=True, required=True, validators=[validate_password])
    password2 = serializers.CharField(write_only=True, required=True, label='Confirm Password')
    
    class Meta:
        model = User
        fields = (
            'email', 'password', 'password2', 'full_name', 'role', 'city',
            'phone_number', 'verification_document_type', 'verification_document_id'
        )
        extra_kwargs = {
            'full_name': {'required': True},
            'role': {'required': True}
        }
    
    def validate(self, attrs):
        """Validate that passwords match"""
        if attrs['password'] != attrs['password2']:
            raise serializers.ValidationError({"password": "Password fields didn't match."})
        return attrs

    def validate_role(self, value):
        if value == 'admin':
            raise serializers.ValidationError('Admin accounts cannot be created via public registration.')
        return value
    
    def create(self, validated_data):
        """Create user with validated data"""
        validated_data.pop('password2')
        try:
            return User.objects.create_user(**validated_data)
        except ValueError as exc:
            raise serializers.ValidationError({'role': str(exc)})


class UserLoginSerializer(serializers.Serializer):
    """Serializer for user login"""
    email = serializers.EmailField(required=True)
    password = serializers.CharField(required=True, write_only=True)


class OAuthCompleteSerializer(serializers.ModelSerializer):
    """Serializer for completing OAuth profile"""
    
    class Meta:
        model = User
        fields = (
            'full_name', 'role', 'city', 'phone_number',
            'verification_document_type', 'verification_document_id'
        )
        extra_kwargs = {
            'full_name': {'required': True},
            'role': {'required': True}
        }
    
    def update(self, instance, validated_data):
        """Update user and mark OAuth as complete"""
        instance.full_name = validated_data.get('full_name', instance.full_name)
        instance.role = validated_data.get('role', instance.role)
        instance.city = validated_data.get('city', instance.city)
        instance.phone_number = validated_data.get('phone_number', instance.phone_number)
        instance.verification_document_type = validated_data.get(
            'verification_document_type',
            instance.verification_document_type,
        )
        instance.verification_document_id = validated_data.get(
            'verification_document_id',
            instance.verification_document_id,
        )
        instance.is_oauth_complete = True
        instance.save()
        return instance

    def validate_role(self, value):
        if value == 'admin':
            raise serializers.ValidationError('Admin role cannot be assigned via OAuth completion.')
        return value


class UserSerializer(serializers.ModelSerializer):
    """Serializer for user details"""
    
    class Meta:
        model = User
        fields = (
            'id', 'email', 'full_name', 'role', 'city', 'phone_number', 'bio',
            'latitude', 'longitude', 'profile_photo', 'google_id', 'oauth_provider',
            'is_oauth_complete', 'verification_document_type',
            'verification_document_id', 'is_verified', 'created_at'
        )
        read_only_fields = (
            'id', 'google_id', 'oauth_provider', 'is_oauth_complete',
            'is_verified', 'created_at'
        )


class UserProfileSerializer(serializers.ModelSerializer):
    """Serializer for authenticated profile updates"""

    class Meta:
        model = User
        fields = (
            'full_name', 'city', 'phone_number', 'bio', 'latitude', 'longitude',
            'verification_document_type', 'verification_document_id'
        )


class UserListSerializer(serializers.ModelSerializer):
    """Serializer for admin user list"""
    
    class Meta:
        model = User
        fields = (
            'id', 'email', 'full_name', 'role', 'city', 'phone_number',
            'is_verified', 'created_at', 'is_active'
        )
        read_only_fields = ('id', 'created_at')


class PasskeySignupOptionsSerializer(serializers.Serializer):
    """Serializer for starting passkey sign-up ceremony."""

    email = serializers.EmailField(required=True)
    full_name = serializers.CharField(required=False, allow_blank=True, max_length=255)
    role = serializers.ChoiceField(choices=User.ROLE_CHOICES, required=False)
    city = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=100)
    phone_number = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=20)
    verification_document_type = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=50,
    )
    verification_document_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=100,
    )

    def validate_role(self, value):
        if value is None:
            return value
        if value == 'admin':
            raise serializers.ValidationError('Admin accounts cannot be created via public registration.')
        return value


class PasskeyLoginOptionsSerializer(serializers.Serializer):
    """Serializer for starting passkey login ceremony."""

    email = serializers.EmailField(required=True)


class PasskeyCredentialVerifySerializer(serializers.Serializer):
    """Serializer for passkey attestation/assertion payloads."""

    credential = serializers.JSONField(required=True)


class PasskeyCredentialSerializer(serializers.ModelSerializer):
    """Serializer for listing stored passkeys for an authenticated user."""

    key_hint = serializers.SerializerMethodField()

    class Meta:
        model = PasskeyCredential
        fields = (
            'id',
            'key_hint',
            'transports',
            'last_used_at',
            'created_at',
        )
        read_only_fields = fields

    def get_key_hint(self, obj):
        return f"{obj.credential_id[:12]}..."
