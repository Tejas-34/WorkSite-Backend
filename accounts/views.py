from rest_framework import status, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.conf import settings
from django.shortcuts import redirect
from django.core import signing
from django.utils.crypto import get_random_string
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
GOOGLE_OAUTH_STATE_SALT = 'worksite-google-oauth-state'
GOOGLE_OAUTH_STATE_MAX_AGE_SECONDS = 600


def _build_redirect_url(base_url, params):
    separator = '&' if '?' in base_url else '?'
    return f"{base_url}{separator}{urlencode(params)}"


def _build_signed_oauth_state(response_mode, next_path=''):
    payload = {
        'nonce': get_random_string(32),
        'response_mode': response_mode,
        'next': next_path or '',
    }
    return signing.dumps(payload, salt=GOOGLE_OAUTH_STATE_SALT)


def _load_signed_oauth_state(state):
    return signing.loads(
        state,
        salt=GOOGLE_OAUTH_STATE_SALT,
        max_age=GOOGLE_OAUTH_STATE_MAX_AGE_SECONDS,
    )


def _normalized_setting(value):
    return (value or '').strip()


def _looks_like_placeholder(value):
    normalized = _normalized_setting(value).lower()
    return normalized.startswith('your-') or normalized in {
        'your-client-id',
        'your-google-client-id',
        'your-google-client-secret',
        'your-secret',
    }


def _wants_redirect_response(request, response_mode='json'):
    if response_mode == 'redirect':
        return True
    accept = request.META.get('HTTP_ACCEPT', '')
    return 'text/html' in accept


def _oauth_error_response(request, message, http_status=status.HTTP_400_BAD_REQUEST, code='oauth_error', response_mode='json'):
    error_redirect_url = getattr(settings, 'GOOGLE_OAUTH_ERROR_URL', '')
    if _wants_redirect_response(request, response_mode) and error_redirect_url:
        return redirect(_build_redirect_url(error_redirect_url, {'error': code, 'message': message}))
    return Response({'error': message}, status=http_status)


def _is_programmatic_redirect_request(request):
    sec_fetch_mode = request.META.get('HTTP_SEC_FETCH_MODE', '')
    return bool(sec_fetch_mode and sec_fetch_mode.lower() != 'navigate')


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
    google_client_id = _normalized_setting(getattr(settings, 'GOOGLE_CLIENT_ID', ''))
    google_redirect_uri = _normalized_setting(getattr(settings, 'GOOGLE_REDIRECT_URI', ''))
    response_mode = 'redirect' if request.query_params.get('mode') == 'redirect' else 'json'

    missing_fields = []
    if not google_client_id or _looks_like_placeholder(google_client_id):
        missing_fields.append('GOOGLE_CLIENT_ID')
    if not google_redirect_uri:
        missing_fields.append('GOOGLE_REDIRECT_URI')
    if response_mode == 'redirect':
        success_url = _normalized_setting(getattr(settings, 'GOOGLE_OAUTH_SUCCESS_URL', ''))
        if not success_url:
            missing_fields.append('GOOGLE_OAUTH_SUCCESS_URL')

    if missing_fields:
        return Response({
            'error': 'Google OAuth is not fully configured',
            'missing_fields': missing_fields,
        }, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    next_path = request.query_params.get('next')
    if not (next_path and next_path.startswith('/')):
        next_path = ''

    state = _build_signed_oauth_state(response_mode=response_mode, next_path=next_path)

    params = {
        'client_id': google_client_id,
        'redirect_uri': google_redirect_uri,
        'response_type': 'code',
        'scope': 'openid email profile',
        'access_type': 'offline',
        'prompt': 'select_account',
        'state': state,
    }
    
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"

    if response_mode == 'redirect' and not _is_programmatic_redirect_request(request):
        return redirect(auth_url)

    return Response({
        'url': auth_url
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([AllowAny])
def google_auth_callback(request):
    """Handle Google OAuth callback"""
    received_state = request.GET.get('state')
    code = request.GET.get('code')
    google_client_id = _normalized_setting(getattr(settings, 'GOOGLE_CLIENT_ID', ''))
    google_client_secret = _normalized_setting(getattr(settings, 'GOOGLE_CLIENT_SECRET', ''))
    google_redirect_uri = _normalized_setting(getattr(settings, 'GOOGLE_REDIRECT_URI', ''))
    response_mode = 'json'
    next_path = ''

    try:
        state_payload = _load_signed_oauth_state(received_state)
        response_mode = state_payload.get('response_mode', 'json')
        next_path = state_payload.get('next') or ''
    except (signing.BadSignature, signing.SignatureExpired, TypeError):
        return _oauth_error_response(
            request,
            'Invalid OAuth state. Please retry login.',
            http_status=status.HTTP_403_FORBIDDEN,
            code='invalid_oauth_state',
            response_mode='json',
        )

    oauth_error = request.GET.get('error')
    if oauth_error:
        error_description = request.GET.get('error_description') or oauth_error
        return _oauth_error_response(
            request,
            f'Google OAuth error: {error_description}',
            http_status=status.HTTP_400_BAD_REQUEST,
            code='google_oauth_error',
            response_mode=response_mode,
        )

    if not code:
        return _oauth_error_response(
            request,
            'No authorization code provided',
            http_status=status.HTTP_400_BAD_REQUEST,
            code='missing_authorization_code',
            response_mode=response_mode,
        )
    
    # Exchange code for tokens
    token_url = 'https://oauth2.googleapis.com/token'
    if not google_client_id or not google_client_secret or not google_redirect_uri:
        return _oauth_error_response(
            request,
            'Google OAuth server configuration is incomplete',
            http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code='missing_server_oauth_config',
            response_mode=response_mode,
        )

    token_data = {
        'code': code,
        'client_id': google_client_id,
        'client_secret': google_client_secret,
        'redirect_uri': google_redirect_uri,
        'grant_type': 'authorization_code'
    }
    
    try:
        token_response = requests.post(token_url, data=token_data, timeout=10)
        token_response.raise_for_status()
        tokens = token_response.json()
        access_token = tokens.get('access_token')
        if not access_token:
            return _oauth_error_response(
                request,
                'Google token response is missing access token',
                http_status=status.HTTP_400_BAD_REQUEST,
                code='missing_access_token',
                response_mode=response_mode,
            )
        
        # Get user info
        user_info_url = 'https://openidconnect.googleapis.com/v1/userinfo'
        headers = {'Authorization': f'Bearer {access_token}'}
        user_info_response = requests.get(user_info_url, headers=headers, timeout=10)
        user_info_response.raise_for_status()
        user_info = user_info_response.json()
        
        # Check if user exists
        google_id = user_info.get('sub') or user_info.get('id')
        email = user_info.get('email')
        email_verified = user_info.get('email_verified', False)
        if not google_id or not email:
            return _oauth_error_response(
                request,
                'Google did not return required user identity data',
                http_status=status.HTTP_400_BAD_REQUEST,
                code='missing_identity_data',
                response_mode=response_mode,
            )

        if not email_verified:
            return _oauth_error_response(
                request,
                'Google account email is not verified',
                http_status=status.HTTP_400_BAD_REQUEST,
                code='unverified_google_email',
                response_mode=response_mode,
            )

        user = User.objects.filter(google_id=google_id).first()
        if user is None:
            existing_user = User.objects.filter(email=email).first()
            if existing_user:
                if existing_user.role == 'admin':
                    return _oauth_error_response(
                        request,
                        'Admin accounts cannot be linked using Google OAuth',
                        http_status=status.HTTP_403_FORBIDDEN,
                        code='admin_oauth_link_blocked',
                        response_mode=response_mode,
                    )

                user = existing_user
                update_fields = ['google_id', 'oauth_provider', 'updated_at']
                user.google_id = google_id
                user.oauth_provider = 'google'

                profile_photo = user_info.get('picture')
                if profile_photo and profile_photo != user.profile_photo:
                    user.profile_photo = profile_photo
                    update_fields.append('profile_photo')

                if not user.full_name and user_info.get('name'):
                    user.full_name = user_info['name']
                    update_fields.append('full_name')

                if not user.is_oauth_complete:
                    user.is_oauth_complete = True
                    update_fields.append('is_oauth_complete')

                user.save(update_fields=update_fields)
            else:
                user = User.objects.create_user(
                    email=email,
                    google_id=google_id,
                    oauth_provider='google',
                    profile_photo=user_info.get('picture'),
                    full_name=user_info.get('name') or email.split('@')[0],
                    role='worker',
                    is_oauth_complete=False,
                )
        else:
            update_fields = []
            if user.oauth_provider != 'google':
                user.oauth_provider = 'google'
                update_fields.append('oauth_provider')

            profile_photo = user_info.get('picture')
            if profile_photo and profile_photo != user.profile_photo:
                user.profile_photo = profile_photo
                update_fields.append('profile_photo')

            if not user.full_name and user_info.get('name'):
                user.full_name = user_info['name']
                update_fields.append('full_name')

            if update_fields:
                update_fields.append('updated_at')
                user.save(update_fields=update_fields)
        
        # Login user
        login(request, user)
        requires_completion = not user.is_oauth_complete
        success_redirect_url = getattr(settings, 'GOOGLE_OAUTH_SUCCESS_URL', '')

        if _wants_redirect_response(request, response_mode):
            if success_redirect_url:
                redirect_params = {
                    'requires_completion': 'true' if requires_completion else 'false',
                }
                if next_path:
                    redirect_params['next'] = next_path
                return redirect(_build_redirect_url(success_redirect_url, redirect_params))

            return _oauth_error_response(
                request,
                'Google OAuth success redirect URL is not configured',
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                code='missing_success_redirect_url',
                response_mode=response_mode,
            )

        return Response({
            'message': 'OAuth successful',
            'user': UserSerializer(user).data,
            'requires_completion': requires_completion,
        }, status=status.HTTP_200_OK)
        
    except requests.RequestException as e:
        return _oauth_error_response(
            request,
            f'OAuth failed: {str(e)}',
            http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code='oauth_request_failed',
            response_mode=response_mode,
        )


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
        return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def all_users_view(request):
    """List all users for selection in group chats"""
    users = User.objects.filter(is_active=True).order_by('full_name')
    serializer = UserListSerializer(users, many=True)
    return Response(serializer.data)
