from rest_framework import status, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.conf import settings
from django.views.decorators.csrf import ensure_csrf_cookie
from .serializers import (
    UserRegistrationSerializer, 
    UserLoginSerializer,
    OAuthCompleteSerializer,
    UserSerializer,
    UserListSerializer,
    UserProfileSerializer,
)
from .permissions import IsAdmin
import requests
from urllib.parse import urlencode

User = get_user_model()


@api_view(['POST'])
@permission_classes([AllowAny])
def register_view(request):
    """Register a new user with email and password"""
    serializer = UserRegistrationSerializer(data=request.data)
    if serializer.is_valid():
        user = serializer.save()
        return Response({
            'message': 'User registered successfully',
            'user': UserSerializer(user).data
        }, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([AllowAny])
def login_view(request):
    """Login user with email and password"""
    serializer = UserLoginSerializer(data=request.data)
    if serializer.is_valid():
        email = serializer.validated_data['email']
        password = serializer.validated_data['password']
        
        user = authenticate(request, username=email, password=password)
        
        if user is not None:
            login(request, user)
            return Response({
                'message': 'Login successful',
                'user': UserSerializer(user).data
            }, status=status.HTTP_200_OK)
        else:
            return Response({
                'error': 'Invalid credentials'
            }, status=status.HTTP_401_UNAUTHORIZED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([AllowAny])
@ensure_csrf_cookie
def csrf_view(request):
    """Set CSRF cookie for SPA clients"""
    return Response({
        'message': 'CSRF cookie set'
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([AllowAny])
def google_auth_initiate(request):
    """Initiate Google OAuth flow"""
    google_client_id = getattr(settings, 'GOOGLE_CLIENT_ID', '')
    google_redirect_uri = getattr(settings, 'GOOGLE_REDIRECT_URI', '')
    
    if not google_client_id or not google_redirect_uri:
        return Response({
            'error': 'Google OAuth not configured'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    params = {
        'client_id': google_client_id,
        'redirect_uri': google_redirect_uri,
        'response_type': 'code',
        'scope': 'openid email profile',
        'access_type': 'offline',
        'prompt': 'consent'
    }
    
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
    
    return Response({
        'url': auth_url
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([AllowAny])
def google_auth_callback(request):
    """Handle Google OAuth callback"""
    code = request.GET.get('code')
    
    if not code:
        return Response({
            'error': 'No authorization code provided'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    # Exchange code for tokens
    token_url = 'https://oauth2.googleapis.com/token'
    token_data = {
        'code': code,
        'client_id': getattr(settings, 'GOOGLE_CLIENT_ID', ''),
        'client_secret': getattr(settings, 'GOOGLE_CLIENT_SECRET', ''),
        'redirect_uri': getattr(settings, 'GOOGLE_REDIRECT_URI', ''),
        'grant_type': 'authorization_code'
    }
    
    try:
        token_response = requests.post(token_url, data=token_data)
        token_response.raise_for_status()
        tokens = token_response.json()
        
        # Get user info
        user_info_url = 'https://www.googleapis.com/oauth2/v2/userinfo'
        headers = {'Authorization': f"Bearer {tokens['access_token']}"}
        user_info_response = requests.get(user_info_url, headers=headers)
        user_info_response.raise_for_status()
        user_info = user_info_response.json()
        
        # Check if user exists
        google_id = user_info.get('id')
        email = user_info.get('email')
        
        try:
            user = User.objects.get(google_id=google_id)
        except User.DoesNotExist:
            # Check if email already exists
            if User.objects.filter(email=email).exists():
                return Response({
                    'error': 'Email already registered with standard auth'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Create new user
            user = User.objects.create(
                email=email,
                google_id=google_id,
                oauth_provider='google',
                profile_photo=user_info.get('picture'),
                full_name=user_info.get('name', ''),
                is_oauth_complete=False
            )
        
        # Login user
        login(request, user)
        
        return Response({
            'message': 'OAuth successful',
            'user': UserSerializer(user).data,
            'requires_completion': not user.is_oauth_complete
        }, status=status.HTTP_200_OK)
        
    except requests.RequestException as e:
        return Response({
            'error': f'OAuth failed: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def oauth_complete_profile(request):
    """Complete OAuth user profile"""
    user = request.user
    
    if user.is_oauth_complete:
        return Response({
            'message': 'Profile already completed'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    serializer = OAuthCompleteSerializer(user, data=request.data, partial=True)
    if serializer.is_valid():
        serializer.save()
        return Response({
            'message': 'Profile completed successfully',
            'user': UserSerializer(user).data
        }, status=status.HTTP_200_OK)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def auth_status(request):
    """Check authentication status"""
    return Response({
        'authenticated': True,
        'user': UserSerializer(request.user).data
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout_view(request):
    """Logout user"""
    logout(request)
    return Response({
        'message': 'Logout successful'
    }, status=status.HTTP_200_OK)


@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def profile_view(request):
    """Update the authenticated user's profile"""
    serializer = UserProfileSerializer(request.user, data=request.data, partial=True)
    if serializer.is_valid():
        serializer.save()
        return Response({
            'message': 'Profile updated successfully',
            'user': UserSerializer(request.user).data
        }, status=status.HTTP_200_OK)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class UserViewSet(viewsets.ModelViewSet):
    """ViewSet for admin user management"""
    queryset = User.objects.all()
    serializer_class = UserListSerializer
    permission_classes = [IsAdmin]
    
    def destroy(self, request, *args, **kwargs):
        """Delete a user"""
        user = self.get_object()
        if user == request.user:
            return Response({
                'error': 'Cannot delete yourself'
            }, status=status.HTTP_400_BAD_REQUEST)
        user.delete()
        return Response({
            'message': 'User deleted successfully'
        }, status=status.HTTP_204_NO_CONTENT)
