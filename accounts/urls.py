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
    path('auth/logout', views.logout_view, name='logout'),
    path('auth/status', views.auth_status, name='auth-status'),
    path('auth/profile', views.profile_view, name='profile'),
    
    # Google OAuth endpoints
    path('auth/google', views.google_auth_initiate, name='google-auth'),
    path('auth/google/callback', views.google_auth_callback, name='google-callback'),
    path('auth/google/complete', views.oauth_complete_profile, name='oauth-complete'),
    
    # Admin user management
    path('', include(router.urls)),
]
