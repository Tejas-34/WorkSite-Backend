from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'users', views.UserViewSet, basename='user')

urlpatterns = [
    # Authentication endpoints
    path('auth/csrf', views.csrf_view, name='csrf'),
    path('auth/register', views.register_view, name='register'),
    path('auth/login', views.login_view, name='login'),
    path('auth/passkey/register/options', views.passkey_register_options_view, name='passkey-register-options'),
    path('auth/passkey/register/verify', views.passkey_register_verify_view, name='passkey-register-verify'),
    path('auth/passkey/complete', views.passkey_complete_profile, name='passkey-complete'),
    path('auth/passkey/enroll/options', views.passkey_enroll_options_view, name='passkey-enroll-options'),
    path('auth/passkey/enroll/verify', views.passkey_enroll_verify_view, name='passkey-enroll-verify'),
    path('auth/passkey/credentials', views.passkey_credentials_view, name='passkey-credentials'),
    path('auth/passkey/credentials/<int:credential_id>', views.passkey_credential_delete_view, name='passkey-credential-delete'),
    path('auth/passkey/login/options', views.passkey_login_options_view, name='passkey-login-options'),
    path('auth/passkey/login/verify', views.passkey_login_verify_view, name='passkey-login-verify'),
    path('auth/logout', views.logout_view, name='logout'),
    path('auth/status', views.auth_status, name='auth-status'),
    path('auth/profile', views.profile_view, name='profile'),
    
    # Google OAuth endpoints
    path('auth/google', views.google_auth_initiate, name='google-auth'),
    path('auth/google/callback', views.google_auth_callback, name='google-callback'),
    path('auth/google/complete', views.oauth_complete_profile, name='oauth-complete'),
    
    # Admin user management
    path('all-users/', views.all_users_view, name='all-users'),
    path('', include(router.urls)),
]
