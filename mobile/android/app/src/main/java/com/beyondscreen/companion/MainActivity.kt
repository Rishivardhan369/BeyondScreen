package com.beyondscreen.companion

import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import com.beyondscreen.companion.databinding.ActivityMainBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.concurrent.TimeUnit

class MainActivity : AppCompatActivity() {
    private lateinit var binding: ActivityMainBinding
    private lateinit var store: SecureStore
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState); binding=ActivityMainBinding.inflate(layoutInflater); setContentView(binding.root); store=SecureStore(this); render()
        binding.permission.setOnClickListener { startActivity(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS)) }
        binding.pair.setOnClickListener { lifecycleScope.launch { runCatching { withContext(Dispatchers.IO){ApiClient().pair(binding.pairingCode.text.toString())} }.onSuccess { store.token=it; render() }.onFailure { binding.preview.text="Pairing failed: ${it.message}" } } }
        binding.sync.setOnClickListener { previewAndSync() }
        binding.autoSync.setOnCheckedChangeListener { _,enabled -> if(enabled) WorkManager.getInstance(this).enqueueUniquePeriodicWork("beyondscreen-sync",ExistingPeriodicWorkPolicy.UPDATE,PeriodicWorkRequestBuilder<SyncWorker>(24,TimeUnit.HOURS).build()) else WorkManager.getInstance(this).cancelUniqueWork("beyondscreen-sync") }
        binding.unpair.setOnClickListener { lifecycleScope.launch { store.token?.let { token -> runCatching { withContext(Dispatchers.IO){ApiClient().revoke(token)} } }; store.clear(); WorkManager.getInstance(this@MainActivity).cancelUniqueWork("beyondscreen-sync"); render() } }
    }
    private fun previewAndSync(){ lifecycleScope.launch { runCatching { UsageCollector(this@MainActivity).collectToday() }.onSuccess { report -> binding.preview.text="${report.reportDate}\n${report.totalMinutes} minutes · ${report.apps.size} apps\nNotifications: ${report.notifications?:"Unavailable"}"; store.token?.let { token -> runCatching { withContext(Dispatchers.IO){ApiClient().sync(report,token)} }.onSuccess { binding.preview.append("\nSynced") }.onFailure { binding.preview.append("\nQueued for retry: ${it.message}"); WorkManager.getInstance(this@MainActivity).enqueue(OneTimeWorkRequestBuilder<SyncWorker>().build()) } } }.onFailure { binding.preview.text="Usage access unavailable. Manual web entry remains available." } } }
    private fun render(){binding.preview.text=if(store.token==null)"Not paired" else "Paired · ready to preview and sync"}
}
