from django.contrib import admin
from .models import PasskeyCredential, User

@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    """Admin interface for User model"""
    list_display = (
        'email', 'full_name', 'role', 'city', 'phone_number',
        'is_verified', 'is_oauth_complete', 'created_at'
    )
    list_filter = ('role', 'is_verified', 'is_oauth_complete', 'oauth_provider', 'created_at')
    search_fields = ('email', 'full_name', 'city', 'phone_number', 'verification_document_id')
    ordering = ('-created_at',)
    readonly_fields = ('created_at', 'updated_at', 'google_id', 'oauth_provider')
    
    fieldsets = (
        ('Account Information', {
            'fields': (
                'email', 'password', 'full_name', 'role', 'city',
                'phone_number', 'bio', 'latitude', 'longitude'
            )
        }),
        ('Verification', {
            'fields': (
                'verification_document_type', 'verification_document_id',
                'is_verified'
            )
        }),
        ('OAuth Information', {
            'fields': ('google_id', 'oauth_provider', 'is_oauth_complete', 'profile_photo'),
            'classes': ('collapse',)
        }),
        ('Permissions', {
            'fields': ('is_active', 'is_staff', 'is_superuser'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'last_login'),
            'classes': ('collapse',)
        }),
    )


@admin.register(PasskeyCredential)
class PasskeyCredentialAdmin(admin.ModelAdmin):
    list_display = ('user', 'credential_id', 'sign_count', 'last_used_at', 'created_at')
    search_fields = ('user__email', 'credential_id')
    list_filter = ('created_at', 'last_used_at')
    readonly_fields = ('created_at', 'updated_at', 'last_used_at')
