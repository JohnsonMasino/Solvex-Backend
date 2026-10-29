from django.urls import path

from . import views
from .views import ProfilePhotoView 

app_name = "profiles"

urlpatterns = [
    path("", views.ProfileView.as_view(), name="profile"),
    path("photo/", ProfilePhotoView.as_view(), name="photo"),
]
