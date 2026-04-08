import base64
import hashlib
import json
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.core import signing
from django.db import IntegrityError, transaction
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.views.decorators.csrf import ensure_csrf_cookie
from .serializers import (
    PasskeyCredentialSerializer,
    PasskeyCredentialVerifySerializer,
    PasskeyLoginOptionsSerializer,
    PasskeySignupOptionsSerializer,
    UserLoginSerializer,
    OAuthCompleteSerializer,
    UserListSerializer,
    UserProfileSerializer,
    UserRegistrationSerializer,
    UserSerializer,
)
from rest_framework import status, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.exceptions import (
    InvalidAuthenticationResponse,
    InvalidRegistrationResponse,
)
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)
from .models import PasskeyCredential
from .permissions import IsAdmin

User = get_user_model()
GOOGLE_OAUTH_STATE_SALT = 'worksite-google-oauth-state'
GOOGLE_OAUTH_STATE_MAX_AGE_SECONDS = 600
PASSKEY_REGISTER_STATE_KEY = 'worksite-passkey-register'
PASSKEY_LOGIN_STATE_KEY = 'worksite-passkey-login'
PASSKEY_ENROLL_STATE_KEY = 'worksite-passkey-enroll'


def _bytes_to_base64url(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def _passkey_user_id_from_email(email):
    return hashlib.sha256(email.lower().encode('utf-8')).digest()


def _get_webauthn_rp_id():
    return _normalized_setting(getattr(settings, 'WEBAUTHN_RP_ID', ''))


def _get_webauthn_rp_name():
    return _normalized_setting(getattr(settings, 'WEBAUTHN_RP_NAME', ''))


def _get_webauthn_origin():
    return _normalized_setting(getattr(settings, 'WEBAUTHN_ORIGIN', ''))


def _webauthn_user_verification_setting():
    if getattr(settings, 'WEBAUTHN_REQUIRE_USER_VERIFICATION', True):
        return UserVerificationRequirement.REQUIRED
    return UserVerificationRequirement.PREFERRED


def _get_passkey_challenge_timeout_seconds():
    configured_timeout = getattr(settings, 'WEBAUTHN_CHALLENGE_TIMEOUT_SECONDS', 300)
    try:
        timeout = int(configured_timeout)
    except (TypeError, ValueError):
        timeout = 300
    return max(timeout, 1)


def _is_passkey_state_expired(state):
    issued_at = state.get('issued_at')
    if issued_at is None:
        return True
    try:
        elapsed = timezone.now().timestamp() - float(issued_at)
    except (TypeError, ValueError):
        return True
    return elapsed > _get_passkey_challenge_timeout_seconds()


def _get_passkey_verification_flag():
    return bool(getattr(settings, 'WEBAUTHN_REQUIRE_USER_VERIFICATION', True))


def _validate_passkey_server_settings():
    rp_id = _get_webauthn_rp_id()
    rp_name = _get_webauthn_rp_name()
    origin = _get_webauthn_origin()
    missing_fields = []
    if not rp_id:
        missing_fields.append('WEBAUTHN_RP_ID')
    if not rp_name:
        missing_fields.append('WEBAUTHN_RP_NAME')
    if not origin:
        missing_fields.append('WEBAUTHN_ORIGIN')
    return rp_id, rp_name, origin, missing_fields


def _extract_transports(credential_payload):
    transports = credential_payload.get('response', {}).get('transports', [])
    return transports if isinstance(transports, list) else []


def _credential_descriptors_for_user(user):
    descriptors = []
    for passkey in user.passkey_credentials.all():
        descriptors.append(
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(passkey.credential_id))
        )
    return descriptors


def _passkey_error_response(message, http_status=status.HTTP_400_BAD_REQUEST):
    return Response({'error': message}, status=http_status)


def _clean_optional_string(value):
    if value is None:
        return ''
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _build_passkey_signup_payload(signup_data):
    email = _clean_optional_string(signup_data.get('email')).lower()
    full_name = _clean_optional_string(signup_data.get('full_name'))
    role = signup_data.get('role')
    city = _clean_optional_string(signup_data.get('city'))
    phone_number = _clean_optional_string(signup_data.get('phone_number'))
    verification_document_type = _clean_optional_string(signup_data.get('verification_document_type'))
    verification_document_id = _clean_optional_string(signup_data.get('verification_document_id'))

    requires_completion = not role or not city or not phone_number or not verification_document_id
    return {
        'email': email,
        'full_name': full_name or email.split('@')[0],
        'role': role or 'worker',
        'city': city or None,
        'phone_number': phone_number or None,
        'verification_document_type': (verification_document_type or 'aadhar') if verification_document_id else None,
        'verification_document_id': verification_document_id or None,
        'oauth_provider': 'passkey',
        'is_oauth_complete': not requires_completion,
    }, requires_completion


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


@api_view(['POST'])
@permission_classes([AllowAny])
def passkey_register_options_view(request):
    """Start passkey sign-up ceremony and return WebAuthn creation options."""
    rp_id, rp_name, _, missing_fields = _validate_passkey_server_settings()
    if missing_fields:
        return Response({
            'error': 'WebAuthn passkey configuration is incomplete',
            'missing_fields': missing_fields,
        }, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    serializer = PasskeySignupOptionsSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    signup_data = dict(serializer.validated_data)
    email = _clean_optional_string(signup_data.get('email')).lower()
    if User.objects.filter(email=email).exists():
        return _passkey_error_response(
            'An account with this email already exists',
            http_status=status.HTTP_409_CONFLICT,
        )

    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=rp_name,
        user_id=_passkey_user_id_from_email(email),
        user_name=email,
        user_display_name=_clean_optional_string(signup_data.get('full_name')) or email.split('@')[0],
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=_webauthn_user_verification_setting(),
        ),
    )
    options_payload = json.loads(options_to_json(options))

    request.session[PASSKEY_REGISTER_STATE_KEY] = {
        'signup_data': signup_data,
        'challenge': options_payload['challenge'],
        'issued_at': timezone.now().timestamp(),
    }
    request.session.modified = True

    return Response({'publicKey': options_payload}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([AllowAny])
def passkey_register_verify_view(request):
    """Verify passkey attestation, create user, and log them in."""
    state = request.session.get(PASSKEY_REGISTER_STATE_KEY)
    if not state:
        return _passkey_error_response('Passkey registration session not found. Start registration again.')
    if _is_passkey_state_expired(state):
        request.session.pop(PASSKEY_REGISTER_STATE_KEY, None)
        return _passkey_error_response('Passkey registration session expired. Start registration again.')

    serializer = PasskeyCredentialVerifySerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    signup_data = state.get('signup_data', {})
    email = _clean_optional_string(signup_data.get('email')).lower()
    if not email:
        request.session.pop(PASSKEY_REGISTER_STATE_KEY, None)
        return _passkey_error_response('Passkey registration session is invalid. Start registration again.')

    if User.objects.filter(email=email).exists():
        request.session.pop(PASSKEY_REGISTER_STATE_KEY, None)
        return _passkey_error_response(
            'An account with this email already exists',
            http_status=status.HTTP_409_CONFLICT,
        )

    credential_payload = serializer.validated_data['credential']
    try:
        verification = verify_registration_response(
            credential=credential_payload,
            expected_challenge=base64url_to_bytes(state['challenge']),
            expected_origin=_get_webauthn_origin(),
            expected_rp_id=_get_webauthn_rp_id(),
            require_user_verification=_get_passkey_verification_flag(),
        )
    except (InvalidRegistrationResponse, ValueError, TypeError, KeyError) as exc:
        return _passkey_error_response(f'Passkey registration failed: {exc}')

    signup_payload, requires_completion = _build_passkey_signup_payload(signup_data)

    try:
        with transaction.atomic():
            user = User.objects.create_user(**signup_payload)
            PasskeyCredential.objects.create(
                user=user,
                credential_id=_bytes_to_base64url(verification.credential_id),
                public_key=_bytes_to_base64url(verification.credential_public_key),
                sign_count=verification.sign_count,
                transports=_extract_transports(credential_payload),
            )
    except IntegrityError:
        return _passkey_error_response(
            'This passkey is already registered with another account.',
            http_status=status.HTTP_409_CONFLICT,
        )

    request.session.pop(PASSKEY_REGISTER_STATE_KEY, None)
    login(request, user)
    return Response({
        'message': 'Passkey registration successful',
        'user': UserSerializer(user).data,
        'requires_completion': requires_completion,
    }, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([AllowAny])
def passkey_login_options_view(request):
    """Start passkey login ceremony and return WebAuthn request options."""
    rp_id, _, _, missing_fields = _validate_passkey_server_settings()
    if missing_fields:
        return Response({
            'error': 'WebAuthn passkey configuration is incomplete',
            'missing_fields': missing_fields,
        }, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    serializer = PasskeyLoginOptionsSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    user = User.objects.filter(email=serializer.validated_data['email'], is_active=True).first()
    if user is None:
        return _passkey_error_response('No active account found for this email.', http_status=status.HTTP_401_UNAUTHORIZED)

    try:
        allow_credentials = _credential_descriptors_for_user(user)
    except (TypeError, ValueError):
        return _passkey_error_response(
            'Stored passkey data is invalid for this account.',
            http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if not allow_credentials:
        return _passkey_error_response('No passkey registered for this account.')

    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=allow_credentials,
        user_verification=_webauthn_user_verification_setting(),
    )
    options_payload = json.loads(options_to_json(options))

    request.session[PASSKEY_LOGIN_STATE_KEY] = {
        'user_id': user.id,
        'challenge': options_payload['challenge'],
        'issued_at': timezone.now().timestamp(),
    }
    request.session.modified = True

    return Response({'publicKey': options_payload}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def passkey_enroll_options_view(request):
    """Start passkey enrollment ceremony for the authenticated user."""
    rp_id, rp_name, _, missing_fields = _validate_passkey_server_settings()
    if missing_fields:
        return Response({
            'error': 'WebAuthn passkey configuration is incomplete',
            'missing_fields': missing_fields,
        }, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    user = request.user
    if user.passkey_credentials.exists():
        return _passkey_error_response(
            'Passkey already configured for this account. Delete existing passkey first.',
            http_status=status.HTTP_409_CONFLICT,
        )

    try:
        exclude_credentials = _credential_descriptors_for_user(user)
    except (TypeError, ValueError):
        return _passkey_error_response(
            'Stored passkey data is invalid for this account.',
            http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=rp_name,
        user_id=_passkey_user_id_from_email(user.email),
        user_name=user.email,
        user_display_name=user.full_name or user.email,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=_webauthn_user_verification_setting(),
        ),
        exclude_credentials=exclude_credentials,
    )
    options_payload = json.loads(options_to_json(options))

    request.session[PASSKEY_ENROLL_STATE_KEY] = {
        'user_id': user.id,
        'challenge': options_payload['challenge'],
        'issued_at': timezone.now().timestamp(),
    }
    request.session.modified = True

    return Response({
        'publicKey': options_payload,
        'passkey_count': user.passkey_credentials.count(),
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def passkey_enroll_verify_view(request):
    """Verify enrollment response and save passkey for authenticated user."""
    state = request.session.get(PASSKEY_ENROLL_STATE_KEY)
    if not state:
        return _passkey_error_response('Passkey enrollment session not found. Start again.')
    if _is_passkey_state_expired(state):
        request.session.pop(PASSKEY_ENROLL_STATE_KEY, None)
        return _passkey_error_response('Passkey enrollment session expired. Start again.')
    if state.get('user_id') != request.user.id:
        request.session.pop(PASSKEY_ENROLL_STATE_KEY, None)
        return _passkey_error_response(
            'Passkey enrollment session does not match current user.',
            http_status=status.HTTP_403_FORBIDDEN,
        )
    if request.user.passkey_credentials.exists():
        request.session.pop(PASSKEY_ENROLL_STATE_KEY, None)
        return _passkey_error_response(
            'Passkey already configured for this account. Delete existing passkey first.',
            http_status=status.HTTP_409_CONFLICT,
        )

    serializer = PasskeyCredentialVerifySerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    credential_payload = serializer.validated_data['credential']
    try:
        verification = verify_registration_response(
            credential=credential_payload,
            expected_challenge=base64url_to_bytes(state['challenge']),
            expected_origin=_get_webauthn_origin(),
            expected_rp_id=_get_webauthn_rp_id(),
            require_user_verification=_get_passkey_verification_flag(),
        )
    except (InvalidRegistrationResponse, ValueError, TypeError, KeyError) as exc:
        return _passkey_error_response(f'Passkey enrollment failed: {exc}')

    try:
        PasskeyCredential.objects.create(
            user=request.user,
            credential_id=_bytes_to_base64url(verification.credential_id),
            public_key=_bytes_to_base64url(verification.credential_public_key),
            sign_count=verification.sign_count,
            transports=_extract_transports(credential_payload),
        )
    except IntegrityError:
        return _passkey_error_response(
            'This passkey is already registered with another account.',
            http_status=status.HTTP_409_CONFLICT,
        )

    request.session.pop(PASSKEY_ENROLL_STATE_KEY, None)
    return Response({
        'message': 'Passkey saved successfully',
        'user': UserSerializer(request.user).data,
        'passkey_count': request.user.passkey_credentials.count(),
    }, status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def passkey_credentials_view(request):
    """List authenticated user's passkey credentials."""
    credentials = request.user.passkey_credentials.all().order_by('-created_at')
    serializer = PasskeyCredentialSerializer(credentials, many=True)
    return Response({
        'credentials': serializer.data,
    }, status=status.HTTP_200_OK)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def passkey_credential_delete_view(request, credential_id):
    """Delete one passkey credential for authenticated user."""
    credential = request.user.passkey_credentials.filter(id=credential_id).first()
    if credential is None:
        return _passkey_error_response(
            'Passkey not found for this account.',
            http_status=status.HTTP_404_NOT_FOUND,
        )

    credential.delete()
    return Response({
        'message': 'Passkey deleted successfully',
        'passkey_count': request.user.passkey_credentials.count(),
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([AllowAny])
def passkey_login_verify_view(request):
    """Verify passkey assertion and log the user in."""
    state = request.session.get(PASSKEY_LOGIN_STATE_KEY)
    if not state:
        return _passkey_error_response('Passkey login session not found. Start login again.')
    if _is_passkey_state_expired(state):
        request.session.pop(PASSKEY_LOGIN_STATE_KEY, None)
        return _passkey_error_response('Passkey login session expired. Start login again.')

    serializer = PasskeyCredentialVerifySerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    credential_payload = serializer.validated_data['credential']
    credential_id = credential_payload.get('id')
    if not credential_id:
        return _passkey_error_response('Credential id is required.')

    passkey_credential = PasskeyCredential.objects.select_related('user').filter(
        credential_id=credential_id,
        user_id=state.get('user_id'),
        user__is_active=True,
    ).first()
    if passkey_credential is None:
        return _passkey_error_response('Passkey login failed.', http_status=status.HTTP_401_UNAUTHORIZED)

    try:
        verification = verify_authentication_response(
            credential=credential_payload,
            expected_challenge=base64url_to_bytes(state['challenge']),
            expected_origin=_get_webauthn_origin(),
            expected_rp_id=_get_webauthn_rp_id(),
            credential_public_key=base64url_to_bytes(passkey_credential.public_key),
            credential_current_sign_count=passkey_credential.sign_count,
            require_user_verification=_get_passkey_verification_flag(),
        )
    except (InvalidAuthenticationResponse, ValueError, TypeError, KeyError) as exc:
        return _passkey_error_response(
            f'Passkey login failed: {exc}',
            http_status=status.HTTP_401_UNAUTHORIZED,
        )

    passkey_credential.sign_count = verification.new_sign_count
    passkey_credential.last_used_at = timezone.now()
    passkey_credential.save(update_fields=['sign_count', 'last_used_at', 'updated_at'])

    request.session.pop(PASSKEY_LOGIN_STATE_KEY, None)
    login(request, passkey_credential.user)
    requires_completion = (
        passkey_credential.user.oauth_provider == 'passkey' and
        not passkey_credential.user.is_oauth_complete
    )
    return Response({
        'message': 'Passkey login successful',
        'user': UserSerializer(passkey_credential.user).data,
        'requires_completion': requires_completion,
    }, status=status.HTTP_200_OK)


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


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def passkey_complete_profile(request):
    """Complete passkey user profile when signup data was partial."""
    user = request.user
    if user.oauth_provider != 'passkey':
        return Response({
            'error': 'Passkey profile completion is only available for passkey accounts'
        }, status=status.HTTP_400_BAD_REQUEST)

    if user.is_oauth_complete:
        return Response({
            'message': 'Profile already completed'
        }, status=status.HTTP_400_BAD_REQUEST)

    serializer = OAuthCompleteSerializer(user, data=request.data, partial=True)
    if serializer.is_valid():
        serializer.save()
        return Response({
            'message': 'Passkey profile completed successfully',
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
