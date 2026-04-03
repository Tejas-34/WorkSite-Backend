from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
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
        )
        self.worker_two = User.objects.create_user(
            email='worker2@test.com',
            password='pass12345',
            full_name='Worker Two',
            role='worker',
            city='Nashik',
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
