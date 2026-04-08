from rest_framework import permissions


class IsWorker(permissions.BasePermission):
    """Permission class to allow only workers"""
    
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == 'worker'


class IsEmployer(permissions.BasePermission):
    """Permission class to allow only employers"""
    
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == 'employer'


class IsAdmin(permissions.BasePermission):
    """Permission class to allow only admins"""
    
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == 'admin'


class IsEmployerOrAdmin(permissions.BasePermission):
    """Permission class to allow employers and admins"""
    
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in ['employer', 'admin']


class IsOwnerOrAdmin(permissions.BasePermission):
    """Permission class to allow only the owner or admin"""
    
    def has_object_permission(self, request, view, obj):
        # Admin can access everything
        if request.user.role == 'admin':
            return True
        
        # Check if user is the owner (works for jobs)
        if hasattr(obj, 'employer'):
            return obj.employer == request.user
        
        # For user objects
        if hasattr(obj, 'email'):
            return obj == request.user
        
        return False
