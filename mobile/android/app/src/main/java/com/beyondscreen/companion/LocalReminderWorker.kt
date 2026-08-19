package com.beyondscreen.companion

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.PackageManager
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationCompat
import androidx.work.Worker
import androidx.work.WorkerParameters

class LocalReminderWorker(context:Context,params:WorkerParameters):Worker(context,params){override fun doWork():Result{if(ActivityCompat.checkSelfPermission(applicationContext,Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED)return Result.success();val manager=applicationContext.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager;manager.createNotificationChannel(NotificationChannel("goal-reminders","BeyondScreen reminders",NotificationManager.IMPORTANCE_DEFAULT));manager.notify(42,NotificationCompat.Builder(applicationContext,"goal-reminders").setSmallIcon(android.R.drawable.ic_popup_reminder).setContentTitle("BeyondScreen").setContentText("Your opted-in Goal time is ready when you are.").build());return Result.success()}}
