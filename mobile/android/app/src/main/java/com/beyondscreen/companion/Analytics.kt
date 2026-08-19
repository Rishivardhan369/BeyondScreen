package com.beyondscreen.companion

import java.time.LocalDate
import java.time.ZoneId
import java.util.UUID

data class AppUsage(val packageName:String,val displayName:String,val minutes:Int,val category:String?=null)
data class MobileReport(val deviceReportId:String=UUID.randomUUID().toString(),val reportDate:String=LocalDate.now().toString(),val timezone:String=ZoneId.systemDefault().id,val totalMinutes:Int,val apps:List<AppUsage>,val pickups:Int?=null,val notifications:Int?=null,val sessions:Int?=null,val longestSessionMinutes:Int?=null,val firstUse:String?=null,val latestUse:String?=null)

object Normalizer {
    fun minutes(milliseconds:Long):Int = (milliseconds.coerceAtLeast(0L)/60000L).toInt()
    fun reportId(existing:String?):String = existing?.takeIf { it.isNotBlank() } ?: UUID.randomUUID().toString()
    fun valid(report:MobileReport):Boolean = report.totalMinutes >= 0 && report.apps.all { it.minutes >= 0 }
}
