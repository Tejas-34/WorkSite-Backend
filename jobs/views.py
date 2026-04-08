from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.db.models import Avg, Q
from django.http import HttpResponse
from django.utils import timezone
import json
from rest_framework import status, viewsets
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from django.db import transaction
from django.db import IntegrityError
from django.db.models import F
from django.db.models.deletion import ProtectedError
from .models import Job, Application, AttendanceRecord, WorkerAvailability, Review, Certificate
from .serializers import (
    JobCreateSerializer,
    JobListSerializer,
    ApplicationSerializer,
    ApplicationStatusUpdateSerializer,
    AttendanceRecordSerializer,
    WorkerAvailabilitySerializer,
    ReviewSerializer,
    TaskCoworkerSerializer,
    CertificateSerializer,
)
from accounts.permissions import IsWorker, IsEmployer, IsEmployerOrAdmin


def _send_worker_selection_email(application):
    worker = application.worker
    job = application.job
    employer = job.employer
    start_date = job.start_date.strftime('%d %b %Y') if job.start_date else 'Not specified'
    deadline = job.deadline.strftime('%d %b %Y') if job.deadline else 'Not specified'
    site = job.site_address or job.site_city or 'Not specified'
    employer_phone = employer.phone_number or 'Not provided'
    employer_city = employer.city or 'Not provided'

    subject = f'Congratulations! You were selected for "{job.title}" on WorkSite'
    plain_body = (
        f'Hello {worker.full_name},\n\n'
        'Great news! Your application has been accepted on WorkSite.\n\n'
        'Job Details:\n'
        f'- Title: {job.title}\n'
        f'- Description: {job.description}\n'
        f'- Daily Wage: {job.daily_wage}\n'
        f'- Work Location: {site}\n'
        f'- Start Date: {start_date}\n'
        f'- Deadline: {deadline}\n'
        f'- Required Workers: {job.required_workers}\n\n'
        'Employer Contact Details:\n'
        f'- Name: {employer.full_name}\n'
        f'- Email: {employer.email}\n'
        f'- Phone: {employer_phone}\n'
        f'- City: {employer_city}\n\n'
        'Please contact the employer to coordinate onboarding and reporting details.\n\n'
        'Regards,\n'
        'WorkSite Team'
    )
    html_body = f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>WorkSite Selection Update</title>
</head>
<body style="margin:0;padding:0;background:#f4f7fb;font-family:Arial,Helvetica,sans-serif;color:#1f2937;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f7fb;padding:24px 0;">
    <tr>
      <td align="center">
        <table role="presentation" width="680" cellpadding="0" cellspacing="0" style="max-width:680px;width:100%;background:#ffffff;border-radius:14px;overflow:hidden;border:1px solid #e5e7eb;">
          <tr>
            <td style="background:linear-gradient(135deg,#0f766e,#2563eb);padding:28px 28px 24px 28px;">
              <div style="font-size:12px;letter-spacing:1px;text-transform:uppercase;color:#d1fae5;font-weight:700;">WorkSite Workforce Network</div>
              <h1 style="margin:10px 0 0 0;color:#ffffff;font-size:24px;line-height:1.3;">You are selected for this job</h1>
              <p style="margin:10px 0 0 0;color:#e0f2fe;font-size:14px;line-height:1.5;">Congratulations {worker.full_name}! Your application has been accepted by the employer.</p>
            </td>
          </tr>
          <tr>
            <td style="padding:24px 28px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e5e7eb;border-radius:10px;background:#f9fafb;">
                <tr>
                  <td style="padding:16px 18px 8px 18px;font-size:13px;font-weight:700;color:#0f172a;text-transform:uppercase;letter-spacing:.4px;">Job Details</td>
                </tr>
                <tr>
                  <td style="padding:0 18px 16px 18px;font-size:14px;line-height:1.7;color:#334155;">
                    <strong>Title:</strong> {job.title}<br/>
                    <strong>Description:</strong> {job.description}<br/>
                    <strong>Daily Wage:</strong> {job.daily_wage}<br/>
                    <strong>Work Location:</strong> {site}<br/>
                    <strong>Start Date:</strong> {start_date}<br/>
                    <strong>Deadline:</strong> {deadline}<br/>
                    <strong>Required Workers:</strong> {job.required_workers}
                  </td>
                </tr>
              </table>

              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:14px;border:1px solid #e5e7eb;border-radius:10px;background:#f9fafb;">
                <tr>
                  <td style="padding:16px 18px 8px 18px;font-size:13px;font-weight:700;color:#0f172a;text-transform:uppercase;letter-spacing:.4px;">Employer Contact</td>
                </tr>
                <tr>
                  <td style="padding:0 18px 16px 18px;font-size:14px;line-height:1.7;color:#334155;">
                    <strong>Name:</strong> {employer.full_name}<br/>
                    <strong>Email:</strong> <a href="mailto:{employer.email}" style="color:#1d4ed8;text-decoration:none;">{employer.email}</a><br/>
                    <strong>Phone:</strong> {employer_phone}<br/>
                    <strong>City:</strong> {employer_city}
                  </td>
                </tr>
              </table>

              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:18px;">
                <tr>
                  <td style="font-size:14px;line-height:1.7;color:#334155;">
                    Please connect with the employer to complete onboarding and reporting details.
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:18px 28px;background:#f8fafc;border-top:1px solid #e5e7eb;font-size:12px;line-height:1.6;color:#64748b;">
              This is an automated notification from WorkSite. Please do not reply to this email.
              <br/>© WorkSite Platform
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""

    email = EmailMultiAlternatives(
        subject=subject,
        body=plain_body,
        to=[worker.email],
    )
    email.attach_alternative(plain_body, 'text/plain')
    email.attach_alternative(html_body, 'text/html')
    email.send(fail_silently=False)


def _build_certificate_text(job, recipient, role_label):
    start_date = job.start_date.strftime('%d %b %Y') if job.start_date else 'Not specified'
    deadline = job.deadline.strftime('%d %b %Y') if job.deadline else 'Not specified'
    completed = (
        timezone.localtime(job.completed_at).strftime('%d %b %Y, %I:%M %p')
        if job.completed_at
        else 'Pending'
    )
    issued_on = timezone.localtime(timezone.now()).strftime('%d %b %Y, %I:%M %p')
    role_display = role_label.replace('_', ' ').title()
    site_name = job.site_address or job.site_city or 'Not specified'
    authority_name = 'WorkSite Platform Administration'
    cert_number = f'WS-{job.id}-{recipient.id}-CC'

    return (
        '============================================================\n'
        '                      WORKSITE PORTAL\n'
        '                 OFFICIAL CERTIFICATE RECORD\n'
        '============================================================\n'
        '                    CERTIFICATE OF COMPLETION\n'
        '\n'
        f'Certificate No. : {cert_number}\n'
        f'Document Type   : Completion Certificate\n'
        f'Issued On       : {issued_on}\n'
        '\n'
        'This is to formally certify that the individual named below\n'
        'has successfully completed the assigned engagement on WorkSite\n'
        'as per platform records and employer verification.\n'
        '\n'
        f'Name of Recipient : {recipient.full_name}\n'
        f'Registered Email  : {recipient.email}\n'
        f'Role in Engagement: {role_display}\n'
        '\n'
        f'Engagement Title  : {job.title}\n'
        f'Employer / Issuer : {job.employer.full_name}\n'
        f'Work Location     : {site_name}\n'
        f'Start Date        : {start_date}\n'
        f'Deadline          : {deadline}\n'
        f'Completion Time   : {completed}\n'
        f'Required Workforce: {job.required_workers}\n'
        f'Filled Workforce  : {job.filled_slots}\n'
        '\n'
        'Verification Notes:\n'
        '- This certificate is generated from immutable platform records.\n'
        '- Any alteration to this text invalidates the document.\n'
        '- For verification, refer to the certificate number above.\n'
        '\n'
        f'Authorized By: {authority_name}\n'
        'Status       : VALID (System Issued)\n'
        '============================================================\n'
    )


def _issue_completion_certificates(job):
    accepted_workers = list(
        Application.objects.filter(job=job, status='accepted').select_related('worker')
    )
    recipients = [(job.employer, 'employer')]
    recipients.extend((application.worker, 'worker') for application in accepted_workers)

    issued = []
    for recipient, role_label in recipients:
        certificate_number = f'WS-{job.id}-{recipient.id}-CC'
        body_text = _build_certificate_text(job, recipient, role_label)
        certificate, created = Certificate.objects.get_or_create(
            job=job,
            recipient=recipient,
            document_type='completion_certificate',
            defaults={
                'certificate_number': certificate_number,
                'subject_name': job.title,
                'issued_to_role': role_label,
                'body_text': body_text,
            },
        )
        if created:
            email = EmailMessage(
                subject=f'WorkSite completion certificate for {job.title}',
                body='Your completion certificate is attached. This email was generated by WorkSite.',
                to=[recipient.email],
            )
            email.attach(
                f'{certificate.certificate_number}.txt',
                certificate.body_text,
                'text/plain',
            )
            email.send(fail_silently=False)
        issued.append(certificate)
    return issued


class JobViewSet(viewsets.ModelViewSet):
    """ViewSet for job management"""
    queryset = Job.objects.select_related('employer').prefetch_related('applications__worker').all()
    permission_classes = [IsAuthenticated]
    
    def get_serializer_class(self):
        """Return appropriate serializer class"""
        if self.action == 'create':
            return JobCreateSerializer
        return JobListSerializer
    
    def get_permissions(self):
        """Set permissions based on action"""
        if self.action == 'create':
            return [IsEmployer()]
        elif self.action in ['destroy', 'update', 'partial_update']:
            return [IsEmployerOrAdmin()]
        return [IsAuthenticated()]
    
    def get_queryset(self):
        """Filter queryset based on query parameters"""
        queryset = super().get_queryset()

        user = self.request.user
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        elif user.role == 'worker':
            queryset = queryset.filter(status='open') | queryset.filter(applications__worker=user)

        city = self.request.query_params.get('city')
        if city:
            queryset = queryset.filter(employer__city=city)

        site_city = self.request.query_params.get('site_city')
        if site_city:
            queryset = queryset.filter(site_city__iexact=site_city)

        min_wage = self.request.query_params.get('min_wage')
        if min_wage:
            queryset = queryset.filter(daily_wage__gte=min_wage)

        max_wage = self.request.query_params.get('max_wage')
        if max_wage:
            queryset = queryset.filter(daily_wage__lte=max_wage)

        skill = self.request.query_params.get('skill')
        if skill:
            queryset = queryset.filter(skills_required__icontains=skill)

        if user.role == 'employer':
            my_jobs = self.request.query_params.get('my_jobs')
            if my_jobs == 'true':
                queryset = queryset.filter(employer=user)

        return queryset.distinct().order_by('-created_at')
    
    def perform_create(self, serializer):
        """Create job with current user as employer"""
        serializer.save(employer=self.request.user)

    def create(self, request, *args, **kwargs):
        """
        Handle malformed frontend payloads where required_workers may arrive as
        nested JSON/string values under worker slots style keys.
        """
        data = request.data.copy()
        if not data.get('required_workers'):
            for key in ('workers', 'worker_count', 'requiredWorkers', 'required-workers'):
                raw_value = data.get(key)
                if raw_value in (None, ''):
                    continue
                parsed_value = raw_value
                if isinstance(raw_value, str):
                    stripped = raw_value.strip()
                    if stripped.startswith('{') or stripped.startswith('['):
                        try:
                            parsed_value = json.loads(stripped)
                        except json.JSONDecodeError:
                            parsed_value = stripped
                    else:
                        parsed_value = stripped

                if isinstance(parsed_value, dict):
                    slots = parsed_value.get('slots') or parsed_value.get('count') or parsed_value.get('required')
                    if slots is not None:
                        data['required_workers'] = slots
                        break
                elif isinstance(parsed_value, (int, str)):
                    data['required_workers'] = parsed_value
                    break

        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        output_serializer = JobListSerializer(serializer.instance, context={'request': request})
        return Response(output_serializer.data, status=status.HTTP_201_CREATED, headers=headers)
    
    def destroy(self, request, *args, **kwargs):
        """Delete job - only owner or admin"""
        job = self.get_object()
        
        # Check if user is owner or admin
        if job.employer != request.user and request.user.role != 'admin':
            return Response({
                'error': 'You do not have permission to delete this job'
            }, status=status.HTTP_403_FORBIDDEN)
        
        try:
            with transaction.atomic():
                job.delete()
        except (ProtectedError, IntegrityError):
            return Response({
                'error': 'Job could not be deleted due to linked records. Remove related dependencies first.'
            }, status=status.HTTP_409_CONFLICT)
        return Response(status=status.HTTP_204_NO_CONTENT)
    
    @action(detail=True, methods=['post'], permission_classes=[IsWorker])
    def apply(self, request, pk=None):
        """Apply for a job - atomic operation to prevent race conditions"""
        job = self.get_object()

        # Use transaction to ensure atomicity
        with transaction.atomic():
            # Lock the job row for update
            job = Job.objects.select_for_update().get(pk=job.pk)

            # Check if job is still open
            if job.status == 'closed':
                return Response({
                    'error': 'This job is no longer accepting applications'
                }, status=status.HTTP_400_BAD_REQUEST)

            # Check if slots are available
            if job.filled_slots >= job.required_workers:
                job.status = 'closed'
                job.save(update_fields=['status', 'updated_at'])
                return Response({
                    'error': 'All positions have been filled'
                }, status=status.HTTP_400_BAD_REQUEST)

            # Create application
            try:
                application = Application.objects.create(
                    job=job,
                    worker=request.user,
                    status='pending'
                )
            except IntegrityError:
                return Response({
                    'error': 'You have already applied for this job'
                }, status=status.HTTP_400_BAD_REQUEST)

            return Response({
                'message': 'Application submitted successfully',
                'application': ApplicationSerializer(application).data
            }, status=status.HTTP_201_CREATED)
    
    @action(detail=True, methods=['get'], permission_classes=[IsEmployerOrAdmin])
    def applications(self, request, pk=None):
        """Get all applications for a job"""
        job = self.get_object()
        
        # Check if user is job owner or admin
        if job.employer != request.user and request.user.role != 'admin':
            return Response({
                'error': 'You do not have permission to view these applications'
            }, status=status.HTTP_403_FORBIDDEN)
        
        applications = job.applications.select_related('worker').all()
        serializer = ApplicationSerializer(applications, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(['PUT'])
@permission_classes([IsEmployerOrAdmin])
def update_application_status(request):
    """Update application status (accept/reject)"""
    serializer = ApplicationStatusUpdateSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    application_id = serializer.validated_data['application_id']
    new_status = serializer.validated_data['status']
    
    try:
        with transaction.atomic():
            application = Application.objects.select_related('job', 'job__employer', 'worker').select_for_update().get(
                id=application_id
            )
            job = Job.objects.select_for_update().get(pk=application.job.pk)

            # Check if user is the job employer
            if job.employer != request.user and request.user.role != 'admin':
                return Response({
                    'error': 'You do not have permission to modify this application'
                }, status=status.HTTP_403_FORBIDDEN)

            if job.completed_at is not None:
                return Response({
                    'error': 'Cannot modify applications for a completed job'
                }, status=status.HTTP_400_BAD_REQUEST)

            old_status = application.status
            application.status = new_status
            send_selection_email = new_status == 'accepted' and old_status != 'accepted'

            # If accepting, increment filled_slots atomically
            if new_status == 'accepted' and old_status != 'accepted':
                if job.status == 'closed' or job.filled_slots >= job.required_workers:
                    return Response({
                        'error': 'All positions have been filled'
                    }, status=status.HTTP_400_BAD_REQUEST)

                # Increment filled_slots using F() to prevent race conditions
                Job.objects.filter(pk=job.pk).update(
                    filled_slots=F('filled_slots') + 1
                )
                job.refresh_from_db(fields=['filled_slots', 'required_workers', 'status', 'completed_at'])

                # Auto-close job if all slots are filled
                if job.filled_slots >= job.required_workers:
                    job.status = 'closed'
                    job.save(update_fields=['status', 'updated_at'])

            # If rejecting a previously accepted application, decrement filled_slots
            elif old_status == 'accepted' and new_status == 'rejected':
                if job.filled_slots > 0:
                    Job.objects.filter(pk=job.pk).update(
                        filled_slots=F('filled_slots') - 1
                    )
                    job.refresh_from_db(fields=['filled_slots', 'required_workers', 'status', 'completed_at'])
                if job.status == 'closed' and job.filled_slots < job.required_workers:
                    job.status = 'open'
                    job.save(update_fields=['status', 'updated_at'])

            application.save(update_fields=['status', 'updated_at'])
            if send_selection_email:
                _send_worker_selection_email(application)

            return Response({
                'message': f'Application {new_status} successfully',
                'application': ApplicationSerializer(application).data
            }, status=status.HTTP_200_OK)

    except Application.DoesNotExist:
        return Response({
            'error': 'Application not found'
        }, status=status.HTTP_404_NOT_FOUND)


@api_view(['DELETE'])
@permission_classes([IsEmployerOrAdmin])
def remove_worker_from_job(request, job_id, worker_id):
    """Remove a worker from a job"""
    try:
        with transaction.atomic():
            job = Job.objects.select_for_update().get(pk=job_id)

            # Check if user is the job employer
            if job.employer != request.user and request.user.role != 'admin':
                return Response({
                    'error': 'You do not have permission to modify this job'
                }, status=status.HTTP_403_FORBIDDEN)

            if job.completed_at is not None:
                return Response({
                    'error': 'Cannot remove workers from a completed job'
                }, status=status.HTTP_400_BAD_REQUEST)

            # Find the application
            application = Application.objects.get(job=job, worker_id=worker_id)

            # If application was accepted, decrement filled_slots safely
            if application.status == 'accepted' and job.filled_slots > 0:
                Job.objects.filter(pk=job.pk).update(
                    filled_slots=F('filled_slots') - 1
                )
                job.refresh_from_db(fields=['filled_slots', 'required_workers', 'status', 'completed_at'])
                if job.status == 'closed' and job.filled_slots < job.required_workers:
                    job.status = 'open'
                    job.save(update_fields=['status', 'updated_at'])

            # Delete the application
            application.delete()

            return Response(status=status.HTTP_204_NO_CONTENT)

    except Job.DoesNotExist:
        return Response({
            'error': 'Job not found'
        }, status=status.HTTP_404_NOT_FOUND)
    except Application.DoesNotExist:
        return Response({
            'error': 'Application not found'
        }, status=status.HTTP_404_NOT_FOUND)


@api_view(['GET'])
@permission_classes([IsWorker])
def my_applications(request):
    """Get current user's applications"""
    applications = Application.objects.filter(worker=request.user).select_related('job', 'job__employer')
    serializer = ApplicationSerializer(applications, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)


class WorkerAvailabilityViewSet(viewsets.ModelViewSet):
    """Workers can manage their own calendar entries."""

    serializer_class = WorkerAvailabilitySerializer
    permission_classes = [IsWorker]

    def get_queryset(self):
        queryset = WorkerAvailability.objects.filter(worker=self.request.user)
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')

        if start_date:
            queryset = queryset.filter(end_date__gte=start_date)
        if end_date:
            queryset = queryset.filter(start_date__lte=end_date)

        return queryset.order_by('start_date', 'end_date')

    def perform_create(self, serializer):
        serializer.save(worker=self.request.user)


@api_view(['GET'])
@permission_classes([IsEmployerOrAdmin])
def worker_availability_for_employers(request):
    """Employers/admins can view all workers' availability entries."""
    from accounts.serializers import UserProfileSerializer

    queryset = WorkerAvailability.objects.select_related('worker').filter(is_blocked=False)

    start_date = request.query_params.get('start_date')
    end_date = request.query_params.get('end_date')
    city = request.query_params.get('city')

    if start_date:
        queryset = queryset.filter(end_date__gte=start_date)
    if end_date:
        queryset = queryset.filter(start_date__lte=end_date)
    if city:
        queryset = queryset.filter(worker__city__iexact=city)

    # Group by worker
    from .serializers import _compute_worker_rating_metrics
    workers_map = {}
    for entry in queryset.order_by('worker_id', 'start_date'):
        worker = entry.worker
        if worker.id not in workers_map:
            metrics = _compute_worker_rating_metrics(worker)
            workers_map[worker.id] = {
                'worker_id': worker.id,
                'worker_name': worker.full_name,
                'worker_email': worker.email,
                'worker_phone': worker.phone_number or '',
                'worker_city': worker.city or '',
                'average_rating': metrics['average_rating'],
                'reviews_count': metrics['reviews_count'],
                'certificate_count': metrics['certificate_count'],
                'availability': [],
            }
        workers_map[worker.id]['availability'].append({
            'id': entry.id,
            'title': entry.title,
            'start_date': str(entry.start_date),
            'end_date': str(entry.end_date),
            'notes': entry.notes or '',
        })

    return Response(list(workers_map.values()), status=status.HTTP_200_OK)


class ReviewViewSet(viewsets.ModelViewSet):
    """Mutual feedback between employers and workers."""

    serializer_class = ReviewSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = Review.objects.select_related('job', 'reviewer', 'reviewee')
        user = self.request.user
        mine = self.request.query_params.get('mine')
        reviewee_id = self.request.query_params.get('reviewee')
        job_id = self.request.query_params.get('job')

        if mine == 'true':
            queryset = queryset.filter(Q(reviewer=user) | Q(reviewee=user))
        if reviewee_id:
            queryset = queryset.filter(reviewee_id=reviewee_id)
        if job_id:
            queryset = queryset.filter(job_id=job_id)
        return queryset

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        job = serializer.validated_data['job']
        reviewee = serializer.validated_data['reviewee']
        reviewer = request.user

        if reviewer == reviewee:
            return Response({'error': 'You cannot review yourself'}, status=status.HTTP_400_BAD_REQUEST)

        accepted_application = Application.objects.filter(
            job=job,
            worker=reviewee if reviewee.role == 'worker' else reviewer,
            status='accepted',
        ).exists()

        is_valid_pair = (
            reviewer == job.employer and reviewee.role == 'worker'
        ) or (
            reviewee == job.employer and reviewer.role == 'worker'
        )

        if not is_valid_pair or not accepted_application:
            return Response({
                'error': 'Reviews are only allowed between the employer and accepted workers for that job'
            }, status=status.HTTP_400_BAD_REQUEST)

        review = serializer.save(reviewer=reviewer)
        output = self.get_serializer(review)
        return Response(output.data, status=status.HTTP_201_CREATED)


class AttendanceRecordView(APIView):
    permission_classes = [IsEmployerOrAdmin]

    def post(self, request, job_id):
        try:
            job = Job.objects.get(pk=job_id)
        except Job.DoesNotExist:
            return Response({'error': 'Job not found'}, status=status.HTTP_404_NOT_FOUND)

        if job.employer != request.user and request.user.role != 'admin':
            return Response({'error': 'You do not have permission to mark attendance'}, status=status.HTTP_403_FORBIDDEN)

        serializer = AttendanceRecordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        application = serializer.validated_data['application']
        if application.job_id != job.id or application.status != 'accepted':
            return Response({
                'error': 'Attendance can only be recorded for accepted workers on this job'
            }, status=status.HTTP_400_BAD_REQUEST)

        attendance, created = AttendanceRecord.objects.update_or_create(
            application=application,
            date=serializer.validated_data['date'],
            defaults={
                'status': serializer.validated_data['status'],
                'notes': serializer.validated_data.get('notes', ''),
            },
        )
        output = AttendanceRecordSerializer(attendance)
        return Response(output.data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsWorker])
def worker_task_history(request):
    """Return ongoing/completed accepted tasks for the current worker."""
    applications = Application.objects.filter(
        worker=request.user,
        status='accepted',
    ).select_related('job', 'job__employer')

    ongoing = applications.filter(Q(job__completed_at__isnull=True))
    completed = applications.filter(job__completed_at__isnull=False)

    return Response({
        'ongoing': ApplicationSerializer(ongoing, many=True, context={'request': request}).data,
        'completed': ApplicationSerializer(completed, many=True, context={'request': request}).data,
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsWorker])
def worker_task_detail(request, job_id):
    """Show co-workers and attendance for one accepted task."""
    try:
        my_application = Application.objects.select_related('job', 'job__employer').get(
            job_id=job_id,
            worker=request.user,
            status='accepted',
        )
    except Application.DoesNotExist:
        return Response({'error': 'Accepted task not found'}, status=status.HTTP_404_NOT_FOUND)

    coworkers = Application.objects.filter(
        job_id=job_id,
        status='accepted',
    ).select_related('worker').prefetch_related('attendance_records')

    return Response({
        'task': ApplicationSerializer(my_application, context={'request': request}).data,
        'coworkers': TaskCoworkerSerializer(coworkers, many=True).data,
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsEmployerOrAdmin])
def mark_job_completed(request, job_id):
    """Allow employer/admin to mark a job completed once work is done."""
    try:
        with transaction.atomic():
            job = Job.objects.select_for_update().get(pk=job_id)
            if job.employer != request.user and request.user.role != 'admin':
                return Response({'error': 'You do not have permission to modify this job'}, status=status.HTTP_403_FORBIDDEN)

            if job.completed_at is None:
                job.completed_at = timezone.now()
                if job.status != 'closed':
                    job.status = 'closed'
                job.save(update_fields=['completed_at', 'status', 'updated_at'])
                _issue_completion_certificates(job)
    except Job.DoesNotExist:
        return Response({'error': 'Job not found'}, status=status.HTTP_404_NOT_FOUND)

    return Response({
        'message': 'Job marked as completed',
        'job': JobListSerializer(job, context={'request': request}).data,
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def dashboard_summary(request):
    """Small dashboard summary with practical counts for the current user."""
    user = request.user

    if user.role == 'worker':
        accepted = Application.objects.filter(worker=user, status='accepted')
        data = {
            'role': user.role,
            'ongoing_tasks': accepted.filter(job__completed_at__isnull=True).count(),
            'completed_tasks': accepted.filter(job__completed_at__isnull=False).count(),
            'pending_applications': Application.objects.filter(worker=user, status='pending').count(),
            'average_rating': user.reviews_received.aggregate(avg=Avg('rating'))['avg'],
            'reviews_count': user.reviews_received.count(),
        }
    else:
        posted_jobs = Job.objects.filter(employer=user)
        data = {
            'role': user.role,
            'posted_jobs': posted_jobs.count(),
            'active_jobs': posted_jobs.filter(completed_at__isnull=True).count(),
            'completed_jobs': posted_jobs.filter(completed_at__isnull=False).count(),
            'pending_applications': Application.objects.filter(job__employer=user, status='pending').count(),
            'average_rating': user.reviews_received.aggregate(avg=Avg('rating'))['avg'],
            'reviews_count': user.reviews_received.count(),
        }

    return Response(data, status=status.HTTP_200_OK)


class CertificateViewSet(viewsets.ReadOnlyModelViewSet):
    """List certificates for the authenticated user."""

    serializer_class = CertificateSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        queryset = Certificate.objects.select_related(
            'recipient', 'job', 'job__employer'
        ).prefetch_related('job__applications__worker')

        if user.role == 'admin':
            return queryset
        return queryset.filter(recipient=user)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def download_certificate(request, certificate_id):
    try:
        certificate = Certificate.objects.select_related('recipient').get(pk=certificate_id)
    except Certificate.DoesNotExist:
        return Response({'error': 'Certificate not found'}, status=status.HTTP_404_NOT_FOUND)

    if request.user.role != 'admin' and certificate.recipient != request.user:
        return Response({'error': 'You do not have permission to access this certificate'}, status=status.HTTP_403_FORBIDDEN)

    response = HttpResponse(certificate.body_text, content_type='text/plain; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{certificate.certificate_number}.txt"'
    return response
