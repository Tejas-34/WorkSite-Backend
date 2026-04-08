from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from django.core import mail
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Application, AttendanceRecord, Certificate, Job, Review, WorkerAvailability

User = get_user_model()


class WorkSiteFeatureTests(APITestCase):
    def setUp(self):
        self.employer = User.objects.create_user(
            email='employer@test.com',
            password='pass12345',
            full_name='Employer',
            role='employer',
            city='Mumbai',
        )
        self.worker = User.objects.create_user(
            email='worker@test.com',
            password='pass12345',
            full_name='Worker',
            role='worker',
            city='Pune',
            phone_number='9999999999',
        )
        self.worker_two = User.objects.create_user(
            email='worker2@test.com',
            password='pass12345',
            full_name='Worker Two',
            role='worker',
            city='Nashik',
            phone_number='8888888888',
        )
        self.job = Job.objects.create(
            employer=self.employer,
            title='Mason',
            description='Need experienced mason workers',
            daily_wage=1200,
            required_workers=2,
            skills_required=['masonry', 'concrete'],
            site_address='Andheri East Site',
            site_city='Mumbai',
            start_date=timezone.localdate(),
            deadline=timezone.localdate() + timedelta(days=7),
        )

    def test_job_filters_support_wage_skill_and_site_city(self):
        self.client.force_authenticate(self.worker)

        response = self.client.get('/api/jobs/', {
            'min_wage': 1000,
            'max_wage': 1500,
            'skill': 'masonry',
            'site_city': 'Mumbai',
        })

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['id'], self.job.id)
        self.assertEqual(response.data['results'][0]['skills_required'], ['masonry', 'concrete'])
        self.assertEqual(response.data['results'][0]['days_remaining'], 7)

    def test_worker_calendar_entry_can_be_created_and_listed(self):
        self.client.force_authenticate(self.worker)

        create_response = self.client.post('/api/calendar/', {
            'title': 'Already booked',
            'start_date': '2026-04-10',
            'end_date': '2026-04-12',
            'is_blocked': True,
            'notes': 'Family event',
        }, format='json')

        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(WorkerAvailability.objects.count(), 1)

        list_response = self.client.get('/api/calendar/', {
            'start_date': '2026-04-11',
            'end_date': '2026-04-15',
        })
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(list_response.data['results']), 1)
        self.assertEqual(list_response.data['results'][0]['title'], 'Already booked')

    def test_task_history_attendance_completion_and_reviews_flow(self):
        application = Application.objects.create(job=self.job, worker=self.worker, status='accepted')
        second_application = Application.objects.create(job=self.job, worker=self.worker_two, status='accepted')
        self.job.filled_slots = 2
        self.job.status = 'closed'
        self.job.save()

        self.client.force_authenticate(self.employer)
        attendance_response = self.client.post(f'/api/jobs/{self.job.id}/attendance', {
            'application': application.id,
            'date': str(timezone.localdate()),
            'status': 'present',
            'notes': 'On time',
        }, format='json')
        self.assertEqual(attendance_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(AttendanceRecord.objects.filter(application=application).count(), 1)

        complete_response = self.client.post(f'/api/jobs/{self.job.id}/complete')
        self.assertEqual(complete_response.status_code, status.HTTP_200_OK)
        self.job.refresh_from_db()
        self.assertIsNotNone(self.job.completed_at)
        self.assertEqual(Certificate.objects.filter(job=self.job).count(), 3)
        worker_certificate = Certificate.objects.filter(job=self.job, recipient=self.worker).first()
        self.assertIsNotNone(worker_certificate)
        self.assertIn('CERTIFICATE OF COMPLETION', worker_certificate.body_text)
        self.assertIn('Certificate No.', worker_certificate.body_text)
        self.assertIn('Status       : VALID (System Issued)', worker_certificate.body_text)

        employer_review_response = self.client.post('/api/reviews/', {
            'job': self.job.id,
            'reviewee': self.worker.id,
            'rating': 5,
            'comment': 'Reliable worker',
        }, format='json')
        self.assertEqual(employer_review_response.status_code, status.HTTP_201_CREATED)

        self.client.force_authenticate(self.worker)
        history_response = self.client.get('/api/applications/tasks')
        self.assertEqual(history_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(history_response.data['completed']), 1)
        self.assertEqual(history_response.data['completed'][0]['job']['id'], self.job.id)

        detail_response = self.client.get(f'/api/applications/tasks/{self.job.id}')
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(detail_response.data['coworkers']), 2)
        worker_coworker = next(
            item for item in detail_response.data['coworkers']
            if item['worker']['id'] == self.worker.id
        )
        self.assertEqual(worker_coworker['attendance'][0]['status'], 'present')

        worker_review_response = self.client.post('/api/reviews/', {
            'job': self.job.id,
            'reviewee': self.employer.id,
            'rating': 4,
            'comment': 'Clear instructions',
        }, format='json')
        self.assertEqual(worker_review_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Review.objects.count(), 2)

        dashboard_response = self.client.get('/api/dashboard/summary')
        self.assertEqual(dashboard_response.status_code, status.HTTP_200_OK)
        self.assertEqual(dashboard_response.data['completed_tasks'], 1)
        self.assertEqual(float(dashboard_response.data['average_rating']), 5.0)

        self.client.force_authenticate(self.employer)
        employer_dashboard = self.client.get('/api/dashboard/summary')
        self.assertEqual(employer_dashboard.status_code, status.HTTP_200_OK)
        self.assertEqual(employer_dashboard.data['completed_jobs'], 1)
        self.assertEqual(float(employer_dashboard.data['average_rating']), 4.0)

        certificates_response = self.client.get('/api/certificates/')
        self.assertEqual(certificates_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(certificates_response.data['results']), 1)

    def test_employer_sees_applicant_email_and_phone_number(self):
        Application.objects.create(job=self.job, worker=self.worker, status='pending')

        self.client.force_authenticate(self.employer)

        applications_response = self.client.get(f'/api/jobs/{self.job.id}/applications/')
        self.assertEqual(applications_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(applications_response.data), 1)
        self.assertEqual(applications_response.data[0]['worker']['email'], 'worker@test.com')
        self.assertEqual(applications_response.data[0]['worker']['phone_number'], '9999999999')

        jobs_response = self.client.get('/api/jobs/', {'my_jobs': 'true'})
        self.assertEqual(jobs_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(jobs_response.data['results']), 1)
        self.assertEqual(len(jobs_response.data['results'][0]['applicants']), 1)
        self.assertEqual(jobs_response.data['results'][0]['applicants'][0]['email'], 'worker@test.com')
        self.assertEqual(jobs_response.data['results'][0]['applicants'][0]['phone_number'], '9999999999')

    def test_create_job_with_required_workers_respects_payload_value(self):
        self.client.force_authenticate(self.employer)
        response = self.client.post('/api/jobs/', {
            'title': 'Scaffold Team',
            'description': 'Need multiple scaffold workers',
            'daily_wage': '900.00',
            'required_workers': 4,
            'skills_required': ['scaffolding'],
            'site_city': 'Mumbai',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['required_workers'], 4)
        self.assertEqual(response.data['available_slots'], 4)

    def test_create_job_accepts_workers_alias_payload(self):
        self.client.force_authenticate(self.employer)
        response = self.client.post('/api/jobs/', {
            'title': 'Concrete Team',
            'description': 'Need concrete workers',
            'daily_wage': '1000.00',
            'workers': {'slots': 4},
            'skills_required': ['concrete'],
            'site_city': 'Pune',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['required_workers'], 4)
        self.assertEqual(response.data['available_slots'], 4)

    def test_worker_rating_gets_certificate_trust_boost_for_employer_views(self):
        # Base review rating for worker
        Review.objects.create(
            reviewer=self.employer,
            reviewee=self.worker,
            job=self.job,
            rating=3,
            comment='Average performance',
        )
        Review.objects.create(
            reviewer=self.employer,
            reviewee=self.worker_two,
            job=self.job,
            rating=3,
            comment='Average performance',
        )

        # Worker one has more completion certificates than worker two
        Certificate.objects.create(
            job=self.job,
            recipient=self.worker,
            document_type='completion_certificate',
            certificate_number='TEST-CC-1',
            subject_name='Mason',
            issued_to_role='worker',
            body_text='Certificate body',
        )
        Certificate.objects.create(
            job=self.job,
            recipient=self.worker_two,
            document_type='completion_certificate',
            certificate_number='TEST-CC-2',
            subject_name='Mason',
            issued_to_role='worker',
            body_text='Certificate body',
        )
        extra_job = Job.objects.create(
            employer=self.employer,
            title='Extra Mason Work',
            description='Second completed work record',
            daily_wage=900,
            required_workers=1,
        )
        Certificate.objects.create(
            job=extra_job,
            recipient=self.worker,
            document_type='completion_certificate',
            certificate_number='TEST-CC-3',
            subject_name='Extra Mason Work',
            issued_to_role='worker',
            body_text='Certificate body',
        )

        Application.objects.create(job=self.job, worker=self.worker, status='pending')
        Application.objects.create(job=self.job, worker=self.worker_two, status='pending')

        self.client.force_authenticate(self.employer)
        response = self.client.get(f'/api/jobs/{self.job.id}/applications/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        worker_one_payload = next(item['worker'] for item in response.data if item['worker']['id'] == self.worker.id)
        worker_two_payload = next(item['worker'] for item in response.data if item['worker']['id'] == self.worker_two.id)

        self.assertEqual(worker_one_payload['certificate_count'], 2)
        self.assertEqual(worker_two_payload['certificate_count'], 1)
        self.assertGreater(worker_one_payload['average_rating'], worker_two_payload['average_rating'])

    def test_accepting_application_sends_selection_email_to_worker(self):
        application = Application.objects.create(job=self.job, worker=self.worker, status='pending')

        self.client.force_authenticate(self.employer)
        response = self.client.put('/api/applications/status', {
            'application_id': application.id,
            'status': 'accepted',
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertIn(self.worker.email, sent.to)
        self.assertIn('You were selected', sent.subject)
        self.assertIn(self.job.title, sent.body)
        self.assertIn(self.employer.full_name, sent.body)
        self.assertIn(self.employer.email, sent.body)
        self.assertEqual(len(sent.alternatives), 2)
        alternative_types = [content_type for _, content_type in sent.alternatives]
        self.assertIn('text/plain', alternative_types)
        self.assertIn('text/html', alternative_types)

    def test_delete_job_succeeds_for_employer(self):
        self.client.force_authenticate(self.employer)
        response = self.client.delete(f'/api/jobs/{self.job.id}/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Job.objects.filter(id=self.job.id).exists())
