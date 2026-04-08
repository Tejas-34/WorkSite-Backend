from django.contrib import admin
from .models import Job, Application, AttendanceRecord, WorkerAvailability, Review, Certificate


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    """Admin interface for Job model"""
    list_display = (
        'title', 'employer', 'site_city', 'daily_wage', 'required_workers',
        'filled_slots', 'status', 'deadline', 'created_at'
    )
    list_filter = ('status', 'site_city', 'created_at')
    search_fields = ('title', 'description', 'site_address', 'employer__full_name', 'employer__email')
    ordering = ('-created_at',)
    readonly_fields = ('created_at', 'updated_at')
    
    fieldsets = (
        ('Job Details', {
            'fields': (
                'site_address', 'site_city'
            )
        }),
        ('Capacity', {
            'fields': ('required_workers', 'filled_slots', 'status')
        }),
        ('Timeline', {
            'fields': ('start_date', 'deadline', 'completed_at')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    """Admin interface for Application model"""
    list_display = ('worker', 'job', 'status', 'applied_at')
    list_filter = ('status', 'applied_at')
    search_fields = ('worker__full_name', 'worker__email', 'job__title')
    ordering = ('-applied_at',)
    readonly_fields = ('applied_at', 'updated_at')
    
    fieldsets = (
        ('Application Details', {
            'fields': ('job', 'worker', 'status')
        }),
        ('Timestamps', {
            'fields': ('applied_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ('application', 'date', 'status', 'created_at')
    list_filter = ('status', 'date')
    search_fields = ('application__worker__full_name', 'application__job__title')


@admin.register(WorkerAvailability)
class WorkerAvailabilityAdmin(admin.ModelAdmin):
    list_display = ('worker', 'title', 'start_date', 'end_date', 'is_blocked')
    list_filter = ('is_blocked', 'start_date')
    search_fields = ('worker__full_name', 'title', 'notes')


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ('job', 'reviewer', 'reviewee', 'rating', 'created_at')
    list_filter = ('rating', 'created_at')
    search_fields = (
        'job__title', 'reviewer__full_name', 'reviewee__full_name', 'comment'
    )


@admin.register(Certificate)
class CertificateAdmin(admin.ModelAdmin):
    list_display = ('certificate_number', 'job', 'recipient', 'document_type', 'issued_at')
    list_filter = ('document_type', 'issued_at')
    search_fields = (
        'certificate_number',
        'subject_name',
        'recipient__full_name',
        'recipient__email',
        'job__title',
    )
    readonly_fields = ('issued_at',)
