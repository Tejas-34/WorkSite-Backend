from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase
from unittest.mock import patch, Mock
from django.test.utils import override_settings

User = get_user_model()


class AccountFeatureTests(APITestCase):
    def test_registration_supports_verification_fields(self):
        response = self.client.post('/api/auth/register', {
            'email': 'worker@test.com',
            'password': 'StrongPass123!',
            'password2': 'StrongPass123!',
            'full_name': 'Verified Worker',
            'role': 'worker',
            'city': 'Mumbai',
            'phone_number': '9999999999',
            'verification_document_type': 'aadhar',
            'verification_document_id': '1234-5678-9999',
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(email='worker@test.com')
        self.assertEqual(user.phone_number, '9999999999')
        self.assertEqual(user.verification_document_type, 'aadhar')
        self.assertEqual(user.verification_document_id, '1234-5678-9999')

    def test_profile_update_supports_location_and_document_fields(self):
        user = User.objects.create_user(
            email='worker2@test.com',
            password='pass12345',
            full_name='Worker Two',
            role='worker',
        )
        self.client.force_authenticate(user)

        response = self.client.put('/api/auth/profile', {
            'city': 'Pune',
            'bio': 'Available for site jobs',
            'latitude': '18.520430',
            'longitude': '73.856743',
            'verification_document_type': 'aadhar',
            'verification_document_id': 'ID-42',
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertEqual(user.city, 'Pune')
        self.assertEqual(user.bio, 'Available for site jobs')
        self.assertEqual(str(user.latitude), '18.520430')
        self.assertEqual(str(user.longitude), '73.856743')

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

    @override_settings(GOOGLE_CLIENT_ID='')
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
        state = initiate.data['url'].split('state=', 1)[1].split('&', 1)[0]

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

        state = initiate['Location'].split('state=', 1)[1].split('&', 1)[0]
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
