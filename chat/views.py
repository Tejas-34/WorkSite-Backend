from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Q
from .models import Message, ChatGroup
from .serializers import MessageSerializer, MessageCreateSerializer, ChatGroupSerializer

class ChatGroupViewSet(viewsets.ModelViewSet):
    queryset = ChatGroup.objects.all()
    serializer_class = ChatGroupSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return ChatGroup.objects.filter(members=self.request.user).order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(admin=self.request.user)

class MessageViewSet(viewsets.ModelViewSet):
    queryset = Message.objects.all()
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        if self.action == 'create':
            return MessageCreateSerializer
        return MessageSerializer

    def get_queryset(self):
        user = self.request.user
        # User is sender OR recipient OR member of the group
        return Message.objects.filter(
            Q(sender=user) | 
            Q(recipient=user) | 
            Q(group__members=user)
        ).distinct().order_by('-timestamp')

    @action(detail=False, methods=['get'])
    def inbox(self, request):
        """Get the latest message from each unique conversation (direct or group)."""
        user = request.user
        
        # 1. Fetch ALL relevant messages
        all_msgs = Message.objects.filter(
            Q(sender=user) | Q(recipient=user) | Q(group__members=user)
        ).distinct().order_by('-timestamp')
        
        # 2. Filter unique threads in Python (Database agnostic)
        seen_threads = set()
        inbox_messages = []
        
        for msg in all_msgs:
            if msg.group_id:
                thread_id = f"group_{msg.group_id}"
            else:
                # For 1-on-1, the thread is the same regardless of who is sender/recipient
                participants = sorted([msg.sender_id, msg.recipient_id])
                thread_id = f"dm_{participants[0]}_{participants[1]}"
                if msg.job_id:
                    thread_id += f"_{msg.job_id}"
            
            if thread_id not in seen_threads:
                inbox_messages.append(msg)
                seen_threads.add(thread_id)
        
        serializer = MessageSerializer(inbox_messages, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def thread(self, request):
        """Get the full conversation thread for direct chat or group."""
        user = request.user
        other_user_id = request.query_params.get('with_user')
        group_id = request.query_params.get('group_id')
        job_id = request.query_params.get('job_id')

        if group_id:
            # Group thread
            queryset = Message.objects.filter(group_id=group_id, group__members=user)
        elif other_user_id:
            # Direct thread
            queryset = Message.objects.filter(
                group__isnull=True
            ).filter(
                (Q(sender=user, recipient_id=other_user_id) | Q(sender_id=other_user_id, recipient=user))
            )
            if job_id:
                queryset = queryset.filter(job_id=job_id)
        else:
            return Response({'error': 'Either with_user or group_id parameter is required'}, status=status.HTTP_400_BAD_REQUEST)

        serializer = MessageSerializer(queryset.order_by('timestamp'), many=True)
        # Mark as read (for direct messages specifically, groups might need more complex read tracking)
        if other_user_id:
            queryset.filter(recipient=user, is_read=False).update(is_read=True)
        
        return Response(serializer.data)
