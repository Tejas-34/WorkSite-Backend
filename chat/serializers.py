from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import Message, ChatGroup

User = get_user_model()

class ChatGroupSerializer(serializers.ModelSerializer):
    admin_name = serializers.ReadOnlyField(source='admin.full_name')
    member_count = serializers.SerializerMethodField()
    members_details = serializers.SerializerMethodField()

    class Meta:
        model = ChatGroup
        fields = ('id', 'name', 'description', 'admin', 'admin_name', 'members', 'members_details', 'member_count', 'created_at')
        read_only_fields = ('id', 'admin', 'created_at')

    def get_member_count(self, obj):
        return obj.members.count()

    def get_members_details(self, obj):
        return [{'id': m.id, 'full_name': m.full_name, 'email': m.email} for m in obj.members.all()]

    def create(self, validated_data):
        members = validated_data.pop('members', [])
        validated_data['admin'] = self.context['request'].user
        group = ChatGroup.objects.create(**validated_data)
        group.members.add(self.context['request'].user) # Add admin as member
        for member in members:
            group.members.add(member)
        return group

class MessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.ReadOnlyField(source='sender.full_name')
    recipient_name = serializers.ReadOnlyField(source='recipient.full_name')
    job_title = serializers.ReadOnlyField(source='job.title')
    group_name = serializers.ReadOnlyField(source='group.name')

    class Meta:
        model = Message
        fields = (
            'id', 'sender', 'sender_name', 'recipient', 'recipient_name',
            'group', 'group_name', 'job', 'job_title', 'content', 'timestamp', 'is_read'
        )
        read_only_fields = ('id', 'sender', 'timestamp', 'is_read')

class MessageCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = ('recipient', 'group', 'job', 'content')

    def validate(self, attrs):
        if not attrs.get('recipient') and not attrs.get('group'):
            raise serializers.ValidationError("Either recipient or group must be specified.")
        if attrs.get('recipient') and attrs.get('group'):
            raise serializers.ValidationError("Cannot specify both recipient and group.")
        return attrs

    def create(self, validated_data):
        validated_data['sender'] = self.context['request'].user
        return super().create(validated_data)
