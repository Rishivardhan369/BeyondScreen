package com.beyondscreen.companion

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject

class ApiClient(private val baseUrl:String=BuildConfig.API_BASE_URL){
    private val client=OkHttpClient(); private val jsonType="application/json".toMediaType()
    fun pair(code:String):String { val body=JSONObject().put("pairing_code",code).put("consent_version","2026-08").put("name","Android companion").put("platform","android").put("app_version",BuildConfig.VERSION_NAME); return call("api/v1/pair/",body,null).getString("device_token") }
    fun revoke(token:String):JSONObject=call("api/v1/device/revoke/",JSONObject(),token)
    fun sync(report:MobileReport,token:String):JSONObject { val apps=JSONArray(); report.apps.forEach { apps.put(JSONObject().put("name",it.displayName).put("package",it.packageName).put("minutes",it.minutes)) }; val body=JSONObject().put("schema_version",1).put("device_report_id",report.deviceReportId).put("report_date",report.reportDate).put("timezone",report.timezone).put("total_minutes",report.totalMinutes).put("apps",apps).putOpt("pickups",report.pickups).putOpt("notifications",report.notifications).putOpt("sessions",report.sessions).putOpt("longest_session_minutes",report.longestSessionMinutes).putOpt("first_use_time",report.firstUse).putOpt("last_use_time",report.latestUse).put("source_type","android_device_sync"); return call("api/v1/mobile-analytics/",body,token) }
    private fun call(path:String,body:JSONObject,token:String?):JSONObject { val builder=Request.Builder().url(baseUrl+path).post(body.toString().toRequestBody(jsonType)); token?.let{builder.header("Authorization","Bearer $it")}; client.newCall(builder.build()).execute().use { response -> val text=response.body?.string().orEmpty(); if(!response.isSuccessful) throw IllegalStateException(JSONObject(text).optString("error","server_error")); return JSONObject(text) } }
}
