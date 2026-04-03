from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'jobs', views.JobViewSet, basename='job')
router.register(r'calendar', views.WorkerAvailabilityViewSet, basename='calendar')
router.register(r'reviews', views.ReviewViewSet, basename='review')
router.register(r'certificates', views.CertificateViewSet, basename='certificate')

urlpatterns = [
    # Application management
    path('applications/status', views.update_application_status, name='update-application-status'),
    path('applications/my', views.my_applications, name='my-applications'),
    path('applications/tasks', views.worker_task_history, name='worker-task-history'),
    path('applications/tasks/<int:job_id>', views.worker_task_detail, name='worker-task-detail'),
    path('jobs/<int:job_id>/applications/<int:worker_id>', views.remove_worker_from_job, name='remove-worker'),
    path('jobs/<int:job_id>/attendance', views.AttendanceRecordView.as_view(), name='attendance-records'),
    path('jobs/<int:job_id>/complete', views.mark_job_completed, name='mark-job-completed'),
    path('certificates/<int:certificate_id>/download', views.download_certificate, name='download-certificate'),
    path('dashboard/summary', views.dashboard_summary, name='dashboard-summary'),
    
    # Job routes (includes apply endpoint as action)
    path('', include(router.urls)),
]
