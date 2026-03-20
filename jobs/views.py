from rest_framework import status, viewsets
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db import transaction
from django.db.models import F
from .models import Job, Application
from .serializers import (
    JobCreateSerializer,
    JobListSerializer,
    ApplicationSerializer,
    ApplicationStatusUpdateSerializer
)
from accounts.permissions import IsWorker, IsEmployer, IsEmployerOrAdmin


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

        if user.role == 'employer':
            my_jobs = self.request.query_params.get('my_jobs')
            if my_jobs == 'true':
                queryset = queryset.filter(employer=user)

        return queryset.distinct().order_by('-created_at')
    
    def perform_create(self, serializer):
        """Create job with current user as employer"""
        serializer.save(employer=self.request.user)
    
    def destroy(self, request, *args, **kwargs):
        """Delete job - only owner or admin"""
        job = self.get_object()
        
        # Check if user is owner or admin
        if job.employer != request.user and request.user.role != 'admin':
            return Response({
                'error': 'You do not have permission to delete this job'
            }, status=status.HTTP_403_FORBIDDEN)
        
        job.delete()
        return Response({
            'message': 'Job deleted successfully'
        }, status=status.HTTP_204_NO_CONTENT)
    
    @action(detail=True, methods=['post'], permission_classes=[IsWorker])
    def apply(self, request, pk=None):
        """Apply for a job - atomic operation to prevent race conditions"""
        job = self.get_object()
        
        # Use transaction to ensure atomicity
        try:
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
                    job.save()
                    return Response({
                        'error': 'All positions have been filled'
                    }, status=status.HTTP_400_BAD_REQUEST)
                
                # Check for duplicate application
                if Application.objects.filter(job=job, worker=request.user).exists():
                    return Response({
                        'error': 'You have already applied for this job'
                    }, status=status.HTTP_400_BAD_REQUEST)
                
                # Create application
                application = Application.objects.create(
                    job=job,
                    worker=request.user,
                    status='pending'
                )
                
                # Note: filled_slots is incremented when application is accepted
                # not when application is submitted
                
                return Response({
                    'message': 'Application submitted successfully',
                    'application': ApplicationSerializer(application).data
                }, status=status.HTTP_201_CREATED)
                
        except Job.DoesNotExist:
            return Response({
                'error': 'Job not found'
            }, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({
                'error': f'Failed to submit application: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
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
    application_id = request.data.get('application_id')
    new_status = request.data.get('status')
    
    if not application_id or not new_status:
        return Response({
            'error': 'application_id and status are required'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    serializer = ApplicationStatusUpdateSerializer(data={'status': new_status})
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        with transaction.atomic():
            application = Application.objects.select_related('job').select_for_update().get(
                id=application_id
            )
            
            # Check if user is the job employer
            if application.job.employer != request.user and request.user.role != 'admin':
                return Response({
                    'error': 'You do not have permission to modify this application'
                }, status=status.HTTP_403_FORBIDDEN)
            
            # Check if job is closed
            if application.job.status == 'closed' and new_status == 'accepted':
                return Response({
                    'error': 'Cannot accept applications for a closed job'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            old_status = application.status
            application.status = new_status
            
            # If accepting, increment filled_slots atomically
            if new_status == 'accepted' and old_status != 'accepted':
                job = Job.objects.select_for_update().get(pk=application.job.pk)
                
                # Check if there are available slots
                if job.filled_slots >= job.required_workers:
                    return Response({
                        'error': 'All positions have been filled'
                    }, status=status.HTTP_400_BAD_REQUEST)
                
                # Increment filled_slots using F() to prevent race conditions
                Job.objects.filter(pk=job.pk).update(
                    filled_slots=F('filled_slots') + 1
                )
                job.refresh_from_db()
                
                # Auto-close job if all slots are filled
                if job.filled_slots >= job.required_workers:
                    job.status = 'closed'
                    job.save()
            
            # If rejecting a previously accepted application, decrement filled_slots
            elif old_status == 'accepted' and new_status == 'rejected':
                Job.objects.filter(pk=application.job.pk).update(
                    filled_slots=F('filled_slots') - 1,
                    status='open'  # Reopen job if it was closed
                )
            
            application.save()
            
            return Response({
                'message': f'Application {new_status} successfully',
                'application': ApplicationSerializer(application).data
            }, status=status.HTTP_200_OK)
            
    except Application.DoesNotExist:
        return Response({
            'error': 'Application not found'
        }, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({
            'error': f'Failed to update application: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


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
            
            # Find the application
            application = Application.objects.get(job=job, worker_id=worker_id)
            
            # If application was accepted, decrement filled_slots
            if application.status == 'accepted':
                Job.objects.filter(pk=job.pk).update(
                    filled_slots=F('filled_slots') - 1,
                    status='open'
                )
            
            # Delete the application
            application.delete()
            
            return Response({
                'message': 'Worker removed from job successfully'
            }, status=status.HTTP_204_NO_CONTENT)
            
    except Job.DoesNotExist:
        return Response({
            'error': 'Job not found'
        }, status=status.HTTP_404_NOT_FOUND)
    except Application.DoesNotExist:
        return Response({
            'error': 'Application not found'
        }, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({
            'error': f'Failed to remove worker: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsWorker])
def my_applications(request):
    """Get current user's applications"""
    applications = Application.objects.filter(worker=request.user).select_related('job', 'job__employer')
    serializer = ApplicationSerializer(applications, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)
