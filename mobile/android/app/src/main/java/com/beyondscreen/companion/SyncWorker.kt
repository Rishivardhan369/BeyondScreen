package com.beyondscreen.companion

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters

class SyncWorker(context:Context,params:WorkerParameters):CoroutineWorker(context,params){override suspend fun doWork():Result{return try{val store=SecureStore(applicationContext);val token=store.token?:return Result.failure();val report=UsageCollector(applicationContext).collectToday();ApiClient().sync(report,token);Result.success()}catch(e:Exception){if(runAttemptCount<5)Result.retry()else Result.failure()}}}
