from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

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
    
    def create(self, validated_data):
        """Create user with validated data"""
        validated_data.pop('password2')
        user = User.objects.create_user(**validated_data)
        return user


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
