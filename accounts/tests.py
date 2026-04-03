from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

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
