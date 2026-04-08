from django.db.models import Avg, Count
from rest_framework import serializers
from .models import Job, Application, AttendanceRecord, WorkerAvailability, Review, Certificate
from django.contrib.auth import get_user_model

User = get_user_model()


def _compute_worker_rating_metrics(worker):
    review_data = worker.reviews_received.aggregate(avg=Avg('rating'), total=Count('id'))
    certificates_count = worker.certificates.filter(
        document_type='completion_certificate',
        issued_to_role='worker',
    ).count()

    reviews_count = review_data['total'] or 0
    review_average = review_data['avg']
    if review_average is None and certificates_count > 0:
        review_average = 3.0

    if review_average is None:
        rating = None
    else:
        trust_boost = min(certificates_count, 20) * 0.10
        rating = round(min(5.0, float(review_average) + trust_boost), 2)

    return {
        'average_rating': rating,
        'reviews_count': reviews_count,
        'certificate_count': certificates_count,
    }


class EmployerSerializer(serializers.ModelSerializer):
    """Serializer for employer details in job listings"""
    
    class Meta:
        model = User
        fields = ('id', 'full_name', 'email', 'phone_number', 'city')


class WorkerSerializer(serializers.ModelSerializer):
    """Serializer for worker details in applications"""
    average_rating = serializers.SerializerMethodField()
    reviews_count = serializers.SerializerMethodField()
    certificate_count = serializers.SerializerMethodField()
    
    class Meta:
        model = User
        fields = (
            'id', 'full_name', 'email', 'phone_number', 'city', 'profile_photo',
            'is_verified', 'average_rating', 'reviews_count', 'certificate_count'
        )

    def _get_metrics(self, obj):
        cache = getattr(self, '_metrics_cache', {})
        if obj.pk not in cache:
            cache[obj.pk] = _compute_worker_rating_metrics(obj)
            self._metrics_cache = cache
        return cache[obj.pk]

    def get_average_rating(self, obj):
        return self._get_metrics(obj)['average_rating']

    def get_reviews_count(self, obj):
        return self._get_metrics(obj)['reviews_count']

    def get_certificate_count(self, obj):
        return self._get_metrics(obj)['certificate_count']


class ApplicationWorkerSummarySerializer(serializers.ModelSerializer):
    """Compact serializer for job cards"""
    application_id = serializers.IntegerField(source='id', read_only=True)
    id = serializers.IntegerField(source='worker.id', read_only=True)
    full_name = serializers.CharField(source='worker.full_name', read_only=True)
    email = serializers.EmailField(source='worker.email', read_only=True)
    phone_number = serializers.CharField(source='worker.phone_number', read_only=True)
    city = serializers.CharField(source='worker.city', read_only=True)
    is_verified = serializers.BooleanField(source='worker.is_verified', read_only=True)
    average_rating = serializers.SerializerMethodField()
    reviews_count = serializers.SerializerMethodField()
    certificate_count = serializers.SerializerMethodField()

    class Meta:
        model = Application
        fields = (
            'application_id', 'id', 'full_name', 'email', 'phone_number', 'city',
            'is_verified', 'average_rating', 'reviews_count', 'certificate_count', 'status'
        )

    def _get_metrics(self, obj):
        cache = getattr(self, '_metrics_cache', {})
        worker_id = obj.worker_id
        if worker_id not in cache:
            cache[worker_id] = _compute_worker_rating_metrics(obj.worker)
            self._metrics_cache = cache
        return cache[worker_id]

    def get_average_rating(self, obj):
        return self._get_metrics(obj)['average_rating']

    def get_reviews_count(self, obj):
        return self._get_metrics(obj)['reviews_count']

    def get_certificate_count(self, obj):
        return self._get_metrics(obj)['certificate_count']


class JobCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating jobs"""
    
    class Meta:
        model = Job
        fields = (
            'title', 'description', 'daily_wage', 'required_workers',
            'skills_required', 'site_address', 'site_city',
            'site_latitude', 'site_longitude', 'start_date', 'deadline'
        )

    def validate(self, attrs):
        start_date = attrs.get('start_date')
        deadline = attrs.get('deadline')
        if start_date and deadline and deadline < start_date:
            raise serializers.ValidationError({
                'deadline': 'Deadline cannot be earlier than the start date.'
            })
        return attrs

    def validate_required_workers(self, value):
        if isinstance(value, bool):
            raise serializers.ValidationError('required_workers must be an integer number, not a boolean.')
        if value < 1:
            raise serializers.ValidationError('required_workers must be at least 1.')
        return value
    
    def create(self, validated_data):
        """Create job with employer from request"""
        request = self.context.get('request')
        validated_data['employer'] = request.user
        return super().create(validated_data)


class JobListSerializer(serializers.ModelSerializer):
    """Serializer for listing jobs"""
    employer = EmployerSerializer(read_only=True)
    available_slots = serializers.IntegerField(read_only=True)
    applicants = serializers.SerializerMethodField()
    applied_workers = serializers.SerializerMethodField()
    my_application_status = serializers.SerializerMethodField()
    days_remaining = serializers.IntegerField(read_only=True)
    
    class Meta:
        model = Job
        fields = (
            'id', 'employer', 'title', 'description', 'daily_wage',
            'required_workers', 'filled_slots', 'available_slots', 'skills_required',
            'site_address', 'site_city', 'site_latitude', 'site_longitude',
            'start_date', 'deadline', 'completed_at', 'days_remaining', 'status', 'created_at',
            'applicants', 'applied_workers', 'my_application_status'
        )
        read_only_fields = ('id', 'filled_slots', 'status', 'created_at')

    def get_applicants(self, obj):
        applications = obj.applications.select_related('worker').all()
        return ApplicationWorkerSummarySerializer(applications, many=True).data

    def get_applied_workers(self, obj):
        return list(obj.applications.values_list('worker_id', flat=True))

    def get_my_application_status(self, obj):
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return None
        application = obj.applications.filter(worker=request.user).only('status').first()
        return application.status if application else None


class ApplicationCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating applications"""
    
    class Meta:
        model = Application
        fields = []  # No fields needed, job and worker are set automatically
    
    def validate(self, attrs):
        """Validate application constraints"""
        request = self.context.get('request')
        job = self.context.get('job')
        
        # Check if job is closed
        if job.status == 'closed':
            raise serializers.ValidationError("This job is no longer accepting applications")
        
        # Check if user has already applied
        if Application.objects.filter(job=job, worker=request.user).exists():
            raise serializers.ValidationError("You have already applied for this job")
        
        return attrs


class ApplicationSerializer(serializers.ModelSerializer):
    """Serializer for application details"""
    worker = WorkerSerializer(read_only=True)
    job = JobListSerializer(read_only=True)
    
    class Meta:
        model = Application
        fields = ('id', 'job', 'worker', 'status', 'applied_at', 'updated_at')
        read_only_fields = ('id', 'applied_at', 'updated_at')


class ApplicationStatusUpdateSerializer(serializers.Serializer):
    """Serializer for updating application status"""
    application_id = serializers.IntegerField(min_value=1)
    status = serializers.ChoiceField(choices=['accepted', 'rejected'])


class AttendanceRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceRecord
        fields = ('id', 'application', 'date', 'status', 'notes', 'created_at')
        read_only_fields = ('id', 'created_at')


class WorkerAvailabilitySerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkerAvailability
        fields = ('id', 'title', 'start_date', 'end_date', 'is_blocked', 'notes', 'created_at')
        read_only_fields = ('id', 'created_at')

    def validate(self, attrs):
        start_date = attrs.get('start_date', getattr(self.instance, 'start_date', None))
        end_date = attrs.get('end_date', getattr(self.instance, 'end_date', None))
        if start_date and end_date and end_date < start_date:
            raise serializers.ValidationError({
                'end_date': 'End date cannot be earlier than the start date.'
            })
        return attrs


class ReviewSerializer(serializers.ModelSerializer):
    job = serializers.PrimaryKeyRelatedField(queryset=Job.objects.all())
    reviewer = WorkerSerializer(read_only=True)
    reviewee = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), write_only=True)
    reviewee_detail = WorkerSerializer(source='reviewee', read_only=True)

    class Meta:
        model = Review
        fields = (
            'id', 'job', 'reviewer', 'reviewee', 'reviewee_detail',
            'rating', 'comment', 'created_at'
        )
        read_only_fields = ('id', 'reviewer', 'created_at')

    def validate(self, attrs):
        rating = attrs.get('rating', getattr(self.instance, 'rating', None))
        if rating is not None and not 1 <= rating <= 5:
            raise serializers.ValidationError({'rating': 'Rating must be between 1 and 5.'})
        return attrs


class TaskCoworkerSerializer(serializers.ModelSerializer):
    application_id = serializers.IntegerField(source='id', read_only=True)
    worker = WorkerSerializer(read_only=True)
    attendance = AttendanceRecordSerializer(source='attendance_records', many=True, read_only=True)

    class Meta:
        model = Application
        fields = ('application_id', 'status', 'worker', 'attendance')


class CertificateSerializer(serializers.ModelSerializer):
    recipient = WorkerSerializer(read_only=True)
    job = JobListSerializer(read_only=True)
    download_path = serializers.SerializerMethodField()

    class Meta:
        model = Certificate
        fields = (
            'id', 'job', 'recipient', 'document_type', 'certificate_number',
            'subject_name', 'issued_to_role', 'body_text', 'issued_at',
            'download_path'
        )

    def get_download_path(self, obj):
        return f'/api/certificates/{obj.id}/download'
