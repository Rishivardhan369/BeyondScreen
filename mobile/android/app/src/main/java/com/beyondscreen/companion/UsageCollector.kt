package com.beyondscreen.companion

import android.app.usage.UsageStatsManager
import android.content.Context
import java.time.LocalDate
import java.time.ZoneId
import java.util.UUID

class UsageCollector(private val context:Context) {
    fun collectToday():MobileReport {
        val zone=ZoneId.systemDefault(); val start=LocalDate.now(zone).atStartOfDay(zone).toInstant().toEpochMilli(); val end=System.currentTimeMillis()
        val manager=context.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
        val apps=manager.queryUsageStats(UsageStatsManager.INTERVAL_DAILY,start,end).filter { it.totalTimeInForeground>0 }.map { AppUsage(it.packageName,it.packageName.substringAfterLast('.'),Normalizer.minutes(it.totalTimeInForeground)) }.sortedByDescending { it.minutes }
        val day=LocalDate.now(zone).toString()
        val stableId=UUID.nameUUIDFromBytes("${context.packageName}:$day".toByteArray()).toString()
        return MobileReport(deviceReportId=stableId,reportDate=day,totalMinutes=apps.sumOf { it.minutes },apps=apps,notifications=NotificationCounterService.count.takeIf { NotificationCounterService.enabled })
    }
}
