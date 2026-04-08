from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models


class UserManager(BaseUserManager):
    """Custom user manager for email-based authentication"""
    
    def create_user(self, email, password=None, **extra_fields):
        """Create and save a regular user"""
        if not email:
            raise ValueError('The Email field must be set')
        if extra_fields.get('role') == 'admin' and not extra_fields.get('is_superuser', False):
            raise ValueError('Use create_superuser to create admin users.')
        email = self.normalize_email(email)
        user = self.model(email=email, username=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user
    
    def create_superuser(self, email, password=None, **extra_fields):
        """Create and save a superuser"""
        if not password:
            raise ValueError('Superuser must have a password.')
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('role', 'admin')
        
        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')
        
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    """Custom User model for WorkSite application"""
    
    ROLE_CHOICES = (
        ('worker', 'Worker'),
        ('employer', 'Employer'),
        ('admin', 'Admin'),
    )
    
    # Override username to make email the primary identifier
    username = models.CharField(max_length=150, unique=False, blank=True, null=True)
    email = models.EmailField(unique=True)
    
    # Core fields
    full_name = models.CharField(max_length=255)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    city = models.CharField(max_length=100, null=True, blank=True)
    phone_number = models.CharField(max_length=20, null=True, blank=True)
    bio = models.TextField(blank=True, default='')
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    # Verification fields
    verification_document_type = models.CharField(max_length=50, null=True, blank=True)
    verification_document_id = models.CharField(max_length=100, null=True, blank=True)
    is_verified = models.BooleanField(default=False)
    
    # OAuth fields
    google_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    oauth_provider = models.CharField(max_length=50, null=True, blank=True)
    is_oauth_complete = models.BooleanField(default=False)
    profile_photo = models.URLField(max_length=500, null=True, blank=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    objects = UserManager()
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['full_name', 'role']
    
    def __str__(self):
        return f"{self.full_name} ({self.email}) - {self.get_role_display()}"
    
    class Meta:
        db_table = 'users'
        ordering = ['-created_at']


class PasskeyCredential(models.Model):
    """Stores a WebAuthn credential for passkey authentication."""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='passkey_credentials',
    )
    credential_id = models.CharField(max_length=512, unique=True)
    public_key = models.TextField()
    sign_count = models.BigIntegerField(default=0)
    transports = models.JSONField(default=list, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'passkey_credentials'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.email} - {self.credential_id[:24]}"
