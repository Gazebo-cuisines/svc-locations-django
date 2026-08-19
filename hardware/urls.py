from django.urls import path

from hardware.views import (
    device_detail,
    device_messages,
    device_post_detail,
    device_posts,
    devices_collection,
    feed_list,
    heartbeat,
    message_ack,
    usage_list,
)

urlpatterns = [
    path('heartbeat/', heartbeat, name='hardware-heartbeat'),
    path('messages/<int:message_id>/ack/', message_ack, name='hardware-message-ack'),
    path('devices/', devices_collection, name='hardware-devices'),
    path('usage/', usage_list, name='hardware-usage'),
    path('feed/', feed_list, name='hardware-feed'),
    path('devices/<str:ident>/posts/', device_posts, name='hardware-device-posts'),
    path(
        'devices/<str:ident>/posts/<int:post_id>/',
        device_post_detail,
        name='hardware-device-post-detail',
    ),
    path(
        'devices/<str:ident>/messages/',
        device_messages,
        name='hardware-device-messages',
    ),
    path('devices/<str:ident>/', device_detail, name='hardware-device-detail'),
]
