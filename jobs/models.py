from django.db import models
from django.conf import settings
from django import VERSION as DJANGO_VERSION
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone


def _reviewer_not_reviewee_constraint():
    kwargs = {'name': 'reviewer_not_reviewee'}
    condition = ~Q(reviewer=models.F('reviewee'))
    if DJANGO_VERSION >= (6, 0):
        kwargs['condition'] = condition
    else:
        kwargs['check'] = condition
    return models.CheckConstraint(**kwargs)


class Job(models.Model):
    """Job posting model"""
    
    STATUS_CHOICES = (
        ('open', 'Open'),
        ('closed', 'Closed'),
    )
    
    employer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='posted_jobs',
        limit_choices_to={'role': 'employer'}
    )
    title = models.CharField(max_length=200)
    description = models.TextField()
    daily_wage = models.DecimalField(max_digits=10, decimal_places=2)
    required_workers = models.PositiveIntegerField()
    filled_slots = models.PositiveIntegerField(default=0)
    skills_required = models.JSONField(default=list, blank=True)
    site_address = models.CharField(max_length=255, blank=True, default='')
    site_city = models.CharField(max_length=100, blank=True, default='')
    site_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    site_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    start_date = models.DateField(null=True, blank=True)
    deadline = models.DateField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='open')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.title} - {self.employer.full_name}"
    
    def clean(self):
        """Validate that filled_slots doesn't exceed required_workers"""
        if self.filled_slots > self.required_workers:
            raise ValidationError('Filled slots cannot exceed required workers')
    
    def save(self, *args, **kwargs):
        """Auto-close job if all slots are filled"""
        if self.filled_slots >= self.required_workers:
            self.status = 'closed'
        super().save(*args, **kwargs)
    
    @property
    def available_slots(self):
        """Return number of available slots"""
        return self.required_workers - self.filled_slots

    @property
    def days_remaining(self):
        if not self.deadline:
            return None
        return (self.deadline - timezone.localdate()).days
    
    class Meta:
        db_table = 'jobs'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['employer']),
        ]


class Application(models.Model):
    """Job application model"""
    
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('rejected', 'Rejected'),
    )
    
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name='applications')
    worker = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='applications',
        limit_choices_to={'role': 'worker'}
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    applied_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.worker.full_name} -> {self.job.title} ({self.status})"
    
    class Meta:
        db_table = 'applications'
        ordering = ['-applied_at']
        unique_together = [['job', 'worker']]  # Prevent duplicate applications
        indexes = [
            models.Index(fields=['job', 'status']),
            models.Index(fields=['worker', 'status']),
        ]


class AttendanceRecord(models.Model):
    """Attendance for accepted workers on a job."""

    STATUS_CHOICES = (
        ('present', 'Present'),
        ('absent', 'Absent'),
        ('half_day', 'Half Day'),
    )

    application = models.ForeignKey(
        Application,
        on_delete=models.CASCADE,
        related_name='attendance_records',
    )
    date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    notes = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'attendance_records'
        ordering = ['-date', '-created_at']
        unique_together = [['application', 'date']]


class WorkerAvailability(models.Model):
    """Calendar blocks and future planning entries for workers."""

    worker = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='availability_entries',
        limit_choices_to={'role': 'worker'},
    )
    title = models.CharField(max_length=120)
    start_date = models.DateField()
    end_date = models.DateField()
    is_blocked = models.BooleanField(default=True)
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if self.end_date < self.start_date:
            raise ValidationError('End date cannot be before start date')

    class Meta:
        db_table = 'worker_availability'
        ordering = ['start_date', 'end_date']


class Review(models.Model):
    """Mutual feedback between employers and workers for accepted jobs."""

    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='reviews_given',
    )
    reviewee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='reviews_received',
    )
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name='reviews')
    rating = models.PositiveSmallIntegerField()
    comment = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if self.reviewer_id == self.reviewee_id:
            raise ValidationError('You cannot review yourself')
        if not 1 <= self.rating <= 5:
            raise ValidationError('Rating must be between 1 and 5')

    class Meta:
        db_table = 'reviews'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['reviewer', 'reviewee', 'job'],
                name='unique_review_per_job_direction',
            ),
            _reviewer_not_reviewee_constraint(),
        ]


class Certificate(models.Model):
    """Completion certificate generated when a job is sealed/completed."""

    DOCUMENT_TYPE_CHOICES = (
        ('completion_certificate', 'Completion Certificate'),
        ('work_agreement', 'Work Agreement'),
    )

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name='certificates')
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='certificates',
    )
    document_type = models.CharField(
        max_length=50,
        choices=DOCUMENT_TYPE_CHOICES,
        default='completion_certificate',
    )
    certificate_number = models.CharField(max_length=40, unique=True)
    subject_name = models.CharField(max_length=255)
    issued_to_role = models.CharField(max_length=20)
    body_text = models.TextField()
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'certificates'
        ordering = ['-issued_at']
        unique_together = [['job', 'recipient', 'document_type']]
