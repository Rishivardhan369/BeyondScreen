package com.beyondscreen.companion

import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification

class NotificationCounterService:NotificationListenerService(){companion object{@Volatile var count=0;@Volatile var enabled=false}override fun onListenerConnected(){enabled=true}override fun onListenerDisconnected(){enabled=false}override fun onNotificationPosted(sbn:StatusBarNotification?){count++ /* Content is intentionally never read. */}}
