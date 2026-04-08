from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import MessageViewSet, ChatGroupViewSet

router = DefaultRouter()
router.register(r'messages', MessageViewSet)
router.register(r'groups', ChatGroupViewSet)

urlpatterns = [
    path('', include(router.urls)),
]
