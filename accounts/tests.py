import base64
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase
from unittest.mock import patch, Mock
from django.test.utils import override_settings
from .models import PasskeyCredential
from urllib.parse import unquote

User = get_user_model()

class AccountFeatureTests(APITestCase):
    @staticmethod
    def _to_base64url(value):
        return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')

    def test_registration_supports_verification_fields(self):
        response = self.client.post('/api/auth/register', {
            'email': 'worker@test.com',
            'password': 'StrongPass123!',
            'password2': 'StrongPass123!',
            'full_name': 'Verified Worker',
            'role': 'worker',
            'city': 'Mumbai',
            'phone_number': '9999999999',
            'date_of_birth': '1990-01-01',
            'verification_document_type': 'Aadhaar',
            'verification_document_id': '123456789012',
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        user = User.objects.get(email='worker@test.com')
        self.assertEqual(user.phone_number, '9999999999')
        self.assertEqual(user.verification_document_type, 'Aadhaar')
        self.assertEqual(user.verification_document_id, '123456789012')

    def test_profile_update_supports_location_and_document_fields(self):
        user = User.objects.create_user(
            email='worker2@test.com',
            password='pass12345',
            full_name='Worker Two',
            role='worker',
            date_of_birth='1990-01-01',
        )
        self.client.force_authenticate(user)

        response = self.client.put('/api/auth/profile', {
            'city': 'Pune',
            'bio': 'Available for site jobs',
            'verification_document_type': 'Aadhaar',
            'verification_document_id': '123456789012',
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertEqual(user.city, 'Pune')
        self.assertEqual(user.bio, 'Available for site jobs')

    def test_google_auth_initiate_sets_state_and_returns_url(self):
        response = self.client.get('/api/auth/google')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('url', response.data)
        self.assertIn('state=', response.data['url'])

    def test_google_auth_initiate_redirect_mode_returns_google_redirect(self):
        response = self.client.get('/api/auth/google?mode=redirect')
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        self.assertIn('accounts.google.com/o/oauth2/v2/auth', response['Location'])
        self.assertIn('state=', response['Location'])

    @override_settings(GOOGLE_CLIENT_ID='', GOOGLE_OAUTH_SUCCESS_URL='')
    def test_google_auth_initiate_returns_missing_config_details(self):
        response = self.client.get('/api/auth/google?mode=redirect')
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertIn('missing_fields', response.data)
        self.assertIn('GOOGLE_CLIENT_ID', response.data['missing_fields'])
        self.assertIn('GOOGLE_OAUTH_SUCCESS_URL', response.data['missing_fields'])

    def test_google_auth_callback_rejects_invalid_state(self):
        response = self.client.get('/api/auth/google/callback', {
            'code': 'dummy-code',
            'state': 'wrong-state',
        })
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data['error'], 'Invalid OAuth state. Please retry login.')

    @patch('accounts.views.requests.get')
    @patch('accounts.views.requests.post')
    def test_google_auth_callback_links_existing_email_user(self, mock_post, mock_get):
        existing_user = User.objects.create_user(
            email='linkme@test.com',
            password='pass12345',
            full_name='Link Me',
            role='worker',
            date_of_birth='1990-01-01',
            is_verified=True,
        )

        token_response = Mock()
        token_response.raise_for_status = Mock()
        token_response.json.return_value = {'access_token': 'token-123'}
        mock_post.return_value = token_response

        user_info_response = Mock()
        user_info_response.raise_for_status = Mock()
        user_info_response.json.return_value = {
            'sub': 'google-sub-1',
            'email': 'linkme@test.com',
            'email_verified': True,
            'name': 'Linked User',
            'picture': 'https://example.com/pic.png',
        }
        mock_get.return_value = user_info_response

        initiate = self.client.get('/api/auth/google')
        self.assertEqual(initiate.status_code, status.HTTP_200_OK)
        state = unquote(initiate.data['url'].split('state=', 1)[1].split('&', 1)[0])

        response = self.client.get('/api/auth/google/callback', {
            'code': 'dummy-code',
            'state': state,
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        existing_user.refresh_from_db()
        self.assertEqual(existing_user.google_id, 'google-sub-1')
        self.assertEqual(existing_user.oauth_provider, 'google')
        self.assertEqual(existing_user.profile_photo, 'https://example.com/pic.png')

    @override_settings(
        GOOGLE_OAUTH_SUCCESS_URL='http://localhost:5173/auth/google/success',
        GOOGLE_OAUTH_ERROR_URL='http://localhost:5173/auth/google/error',
    )
    @patch('accounts.views.requests.get')
    @patch('accounts.views.requests.post')
    def test_google_auth_callback_redirect_mode_redirects_to_frontend(self, mock_post, mock_get):
        token_response = Mock()
        token_response.raise_for_status = Mock()
        token_response.json.return_value = {'access_token': 'token-redirect'}
        mock_post.return_value = token_response

        user_info_response = Mock()
        user_info_response.raise_for_status = Mock()
        user_info_response.json.return_value = {
            'sub': 'google-sub-redirect',
            'email': 'redirect@test.com',
            'email_verified': True,
            'name': 'Redirect User',
            'picture': 'https://example.com/redirect.png',
        }
        mock_get.return_value = user_info_response

        initiate = self.client.get('/api/auth/google?mode=redirect&next=/dashboard')
        self.assertEqual(initiate.status_code, status.HTTP_302_FOUND)
        self.assertIn('accounts.google.com/o/oauth2/v2/auth', initiate['Location'])

        state = unquote(initiate['Location'].split('state=', 1)[1].split('&', 1)[0])
        callback = self.client.get('/api/auth/google/callback', {
            'code': 'dummy-code',
            'state': state,
        }, HTTP_ACCEPT='text/html')

        self.assertEqual(callback.status_code, status.HTTP_302_FOUND)
        self.assertIn('http://localhost:5173/auth/google/success', callback['Location'])
        self.assertIn('requires_completion=true', callback['Location'])
        self.assertIn('next=%2Fdashboard', callback['Location'])

    def test_google_auth_initiate_redirect_mode_with_cors_fetch_returns_json_url(self):
        response = self.client.get('/api/auth/google?mode=redirect', HTTP_SEC_FETCH_MODE='cors')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('url', response.data)
        self.assertIn('accounts.google.com/o/oauth2/v2/auth', response.data['url'])

    def test_passkey_register_options_returns_creation_options(self):
        response = self.client.post('/api/auth/passkey/register/options', {
            'email': 'passkey-new@test.com',
            'full_name': 'Passkey User',
            'role': 'worker',
            'city': 'Pune',
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('publicKey', response.data)
        self.assertIn('challenge', response.data['publicKey'])

    @patch('accounts.views.verify_registration_response')
    def test_passkey_register_verify_creates_user_and_credential(self, mock_verify_registration):
        mock_verify_registration.return_value = Mock(
            credential_id=b'cred-register',
            credential_public_key=b'pubkey-register',
            sign_count=0,
        )

        options_response = self.client.post('/api/auth/passkey/register/options', {
            'email': 'passkey-signup@test.com',
            'full_name': 'Passkey Signup',
            'role': 'worker',
            'city': 'Mumbai',
            'phone_number': '9999999999',
            'verification_document_type': 'aadhar',
            'verification_document_id': 'PASSKEY-ID-1',
        }, format='json')
        self.assertEqual(options_response.status_code, status.HTTP_200_OK)

        verify_response = self.client.post('/api/auth/passkey/register/verify', {
            'credential': {
                'id': self._to_base64url(b'cred-register'),
                'response': {
                    'transports': ['internal'],
                },
            },
        }, format='json')

        self.assertEqual(verify_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(verify_response.data['requires_completion'], False)
        user = User.objects.get(email='passkey-signup@test.com')
        credential = PasskeyCredential.objects.get(user=user)
        self.assertEqual(credential.credential_id, self._to_base64url(b'cred-register'))
        self.assertEqual(credential.transports, ['internal'])
        self.assertEqual(user.city, 'Mumbai')
        self.assertEqual(user.phone_number, '9999999999')
        self.assertEqual(user.verification_document_id, 'PASSKEY-ID-1')
        self.assertEqual(user.oauth_provider, 'passkey')
        self.assertEqual(user.is_oauth_complete, True)

    @patch('accounts.views.verify_registration_response')
    def test_passkey_register_verify_with_partial_data_requires_completion(self, mock_verify_registration):
        mock_verify_registration.return_value = Mock(
            credential_id=b'cred-register-partial',
            credential_public_key=b'pubkey-register-partial',
            sign_count=0,
        )

        options_response = self.client.post('/api/auth/passkey/register/options', {
            'email': 'passkey-partial@test.com',
        }, format='json')
        self.assertEqual(options_response.status_code, status.HTTP_200_OK)

        verify_response = self.client.post('/api/auth/passkey/register/verify', {
            'credential': {
                'id': self._to_base64url(b'cred-register-partial'),
                'response': {
                    'transports': ['internal'],
                },
            },
        }, format='json')

        self.assertEqual(verify_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(verify_response.data['requires_completion'], True)
        user = User.objects.get(email='passkey-partial@test.com')
        self.assertEqual(user.role, 'worker')
        self.assertEqual(user.oauth_provider, 'passkey')
        self.assertEqual(user.is_oauth_complete, False)

    def test_passkey_login_options_returns_request_options(self):
        user = User.objects.create_user(
            email='passkey-login-options@test.com',
            full_name='Passkey Login',
            role='worker',
        )
        PasskeyCredential.objects.create(
            user=user,
            credential_id=self._to_base64url(b'cred-login-options'),
            public_key=self._to_base64url(b'pubkey-login-options'),
            sign_count=1,
            transports=['internal'],
        )

        response = self.client.post('/api/auth/passkey/login/options', {
            'email': 'passkey-login-options@test.com',
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('publicKey', response.data)
        self.assertIn('challenge', response.data['publicKey'])

    def test_passkey_login_options_without_email_returns_discoverable_options(self):
        user = User.objects.create_user(
            email='passkey-login-picker@test.com',
            full_name='Passkey Picker',
            role='worker',
        )
        PasskeyCredential.objects.create(
            user=user,
            credential_id=self._to_base64url(b'cred-login-picker'),
            public_key=self._to_base64url(b'pubkey-login-picker'),
            sign_count=1,
            transports=['internal'],
        )

        response = self.client.post('/api/auth/passkey/login/options', {}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('publicKey', response.data)
        self.assertIn('challenge', response.data['publicKey'])
        self.assertFalse(response.data['publicKey'].get('allowCredentials'))

    @patch('accounts.views.verify_authentication_response')
    def test_passkey_login_verify_updates_sign_count(self, mock_verify_authentication):
        mock_verify_authentication.return_value = Mock(new_sign_count=8)
        user = User.objects.create_user(
            email='passkey-login-verify@test.com',
            full_name='Passkey Verify',
            role='worker',
            oauth_provider='passkey',
            is_oauth_complete=False,
        )
        credential_id = self._to_base64url(b'cred-login-verify')
        passkey = PasskeyCredential.objects.create(
            user=user,
            credential_id=credential_id,
            public_key=self._to_base64url(b'pubkey-login-verify'),
            sign_count=2,
            transports=['internal'],
        )

        options_response = self.client.post('/api/auth/passkey/login/options', {
            'email': 'passkey-login-verify@test.com',
        }, format='json')
        self.assertEqual(options_response.status_code, status.HTTP_200_OK)

        verify_response = self.client.post('/api/auth/passkey/login/verify', {
            'credential': {
                'id': credential_id,
                'response': {},
            },
        }, format='json')

        self.assertEqual(verify_response.status_code, status.HTTP_200_OK)
        self.assertEqual(verify_response.data['requires_completion'], True)
        passkey.refresh_from_db()
        self.assertEqual(passkey.sign_count, 8)

    @patch('accounts.views.verify_authentication_response')
    def test_passkey_login_verify_without_email_updates_sign_count(self, mock_verify_authentication):
        mock_verify_authentication.return_value = Mock(new_sign_count=11)
        user = User.objects.create_user(
            email='passkey-login-picker-verify@test.com',
            full_name='Passkey Picker Verify',
            role='worker',
            oauth_provider='passkey',
            is_oauth_complete=True,
        )
        credential_id = self._to_base64url(b'cred-login-picker-verify')
        passkey = PasskeyCredential.objects.create(
            user=user,
            credential_id=credential_id,
            public_key=self._to_base64url(b'pubkey-login-picker-verify'),
            sign_count=3,
            transports=['internal'],
        )

        options_response = self.client.post('/api/auth/passkey/login/options', {}, format='json')
        self.assertEqual(options_response.status_code, status.HTTP_200_OK)

        verify_response = self.client.post('/api/auth/passkey/login/verify', {
            'credential': {
                'id': credential_id,
                'response': {},
            },
        }, format='json')

        self.assertEqual(verify_response.status_code, status.HTTP_200_OK)
        self.assertEqual(verify_response.data['requires_completion'], False)
        passkey.refresh_from_db()
        self.assertEqual(passkey.sign_count, 11)

    def test_passkey_enroll_options_requires_authentication(self):
        response = self.client.post('/api/auth/passkey/enroll/options', {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_passkey_enroll_options_returns_creation_options_for_authenticated_user(self):
        user = User.objects.create_user(
            email='passkey-enroll-options@test.com',
            full_name='Passkey Enroll',
            role='worker',
        )
        self.client.force_login(user)

        response = self.client.post('/api/auth/passkey/enroll/options', {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('publicKey', response.data)
        self.assertIn('challenge', response.data['publicKey'])
        self.assertEqual(response.data['passkey_count'], 0)

    def test_passkey_enroll_options_rejects_when_passkey_exists(self):
        user = User.objects.create_user(
            email='passkey-enroll-existing@test.com',
            full_name='Passkey Existing',
            role='worker',
        )
        PasskeyCredential.objects.create(
            user=user,
            credential_id=self._to_base64url(b'cred-enroll-existing'),
            public_key=self._to_base64url(b'pubkey-enroll-existing'),
            sign_count=4,
            transports=['internal'],
        )
        self.client.force_login(user)

        response = self.client.post('/api/auth/passkey/enroll/options', {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn('already configured', response.data['error'])

    @patch('accounts.views.verify_registration_response')
    def test_passkey_enroll_verify_creates_credential_for_authenticated_user(self, mock_verify_registration):
        mock_verify_registration.return_value = Mock(
            credential_id=b'cred-enroll',
            credential_public_key=b'pubkey-enroll',
            sign_count=3,
        )
        user = User.objects.create_user(
            email='passkey-enroll-verify@test.com',
            full_name='Passkey Enroll Verify',
            role='worker',
        )
        self.client.force_login(user)

        options_response = self.client.post('/api/auth/passkey/enroll/options', {}, format='json')
        self.assertEqual(options_response.status_code, status.HTTP_200_OK)

        verify_response = self.client.post('/api/auth/passkey/enroll/verify', {
            'credential': {
                'id': self._to_base64url(b'cred-enroll'),
                'response': {
                    'transports': ['hybrid'],
                },
            },
        }, format='json')
        self.assertEqual(verify_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(verify_response.data['passkey_count'], 1)

        credential = PasskeyCredential.objects.get(user=user, credential_id=self._to_base64url(b'cred-enroll'))
        self.assertEqual(credential.sign_count, 3)
        self.assertEqual(credential.transports, ['hybrid'])

    def test_passkey_credentials_list_returns_user_credentials(self):
        user = User.objects.create_user(
            email='passkey-list@test.com',
            full_name='Passkey List',
            role='worker',
        )
        other_user = User.objects.create_user(
            email='passkey-list-other@test.com',
            full_name='Passkey List Other',
            role='worker',
        )
        credential = PasskeyCredential.objects.create(
            user=user,
            credential_id=self._to_base64url(b'cred-list'),
            public_key=self._to_base64url(b'pubkey-list'),
            sign_count=1,
            transports=['internal'],
        )
        PasskeyCredential.objects.create(
            user=other_user,
            credential_id=self._to_base64url(b'cred-list-other'),
            public_key=self._to_base64url(b'pubkey-list-other'),
            sign_count=2,
            transports=['usb'],
        )
        self.client.force_login(user)

        response = self.client.get('/api/auth/passkey/credentials')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['credentials']), 1)
        self.assertEqual(response.data['credentials'][0]['id'], credential.id)

    def test_passkey_credential_delete_removes_credential(self):
        user = User.objects.create_user(
            email='passkey-delete@test.com',
            full_name='Passkey Delete',
            role='worker',
        )
        credential = PasskeyCredential.objects.create(
            user=user,
            credential_id=self._to_base64url(b'cred-delete'),
            public_key=self._to_base64url(b'pubkey-delete'),
            sign_count=9,
            transports=['hybrid'],
        )
        self.client.force_login(user)

        response = self.client.delete(f'/api/auth/passkey/credentials/{credential.id}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['passkey_count'], 0)
        self.assertFalse(PasskeyCredential.objects.filter(id=credential.id).exists())

    def test_passkey_complete_profile_updates_incomplete_passkey_user(self):
        user = User.objects.create_user(
            email='passkey-complete@test.com',
            full_name='Passkey Complete',
            role='worker',
            oauth_provider='passkey',
            is_oauth_complete=False,
        )
        self.client.force_login(user)

        response = self.client.post('/api/auth/passkey/complete', {
            'role': 'employer',
            'city': 'Pune',
            'phone_number': '8888888888',
            'verification_document_type': 'aadhar',
            'verification_document_id': 'COMPLETE-42',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        user.refresh_from_db()
        self.assertEqual(user.role, 'employer')
        self.assertEqual(user.city, 'Pune')
        self.assertEqual(user.phone_number, '8888888888')
        self.assertEqual(user.verification_document_id, 'COMPLETE-42')
        self.assertEqual(user.is_oauth_complete, True)
