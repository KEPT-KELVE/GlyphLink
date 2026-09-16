package com.adam.glyphlink

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.os.BatteryManager
import android.os.Build
import android.os.IBinder
import android.util.Log
import android.app.Notification
import java.io.BufferedReader
import java.io.BufferedWriter
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketTimeoutException
import kotlin.concurrent.thread

class GlyphLinkService : Service() {
    companion object {
        const val PREFS = "glyphlink_status"
        const val KEY_RUNNING = "running"
        const val KEY_CONNECTED = "connected"
        const val KEY_LAST_LABEL = "last_label"

        private const val CHANNEL_ID = "glyphlink"
        private const val NOTIFICATION_ID = 1
        private const val PORT = 47999
        private const val TAG = "GlyphLinkService"
        private const val WATCHDOG_MS = 900L
    }

    private lateinit var glyph: GlyphBridge
    @Volatile private var running = false
    @Volatile private var lastFrameMs = 0L
    private var serverSocket: ServerSocket? = null
    private var clientSocket: Socket? = null

    override fun onCreate() {
        super.onCreate()

        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "GlyphLink", NotificationManager.IMPORTANCE_LOW)
        )

        val notification = Notification.Builder(this, CHANNEL_ID)
            .setContentTitle("GlyphLink active")
            .setContentText("Ready for USB connection")
            .setSmallIcon(android.R.drawable.ic_media_play)
            .setOngoing(true)
            .build()

        val fgsType = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
        } else 0

        startForeground(NOTIFICATION_ID, notification, fgsType)
        setServiceState(running = true, connected = false, label = "")

        glyph = GlyphBridge(this)
        glyph.connect()
        running = true
        startTcpServer()
        startWatchdog()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY

    private fun setServiceState(running: Boolean? = null, connected: Boolean? = null, label: String? = null) {
        val edit = getSharedPreferences(PREFS, MODE_PRIVATE).edit()
        running?.let { edit.putBoolean(KEY_RUNNING, it) }
        connected?.let { edit.putBoolean(KEY_CONNECTED, it) }
        label?.let { edit.putString(KEY_LAST_LABEL, it) }
        edit.apply()
    }

    private fun startTcpServer() {
        thread(name = "GlyphLink-TCP") {
            try {
                serverSocket = ServerSocket(PORT, 1, java.net.InetAddress.getByName("127.0.0.1"))
                while (running) {
                    val socket = serverSocket?.accept() ?: break
                    clientSocket = socket
                    socket.soTimeout = 1000
                    setServiceState(connected = true, label = "USB / ADB")

                    try {
                        val reader = BufferedReader(InputStreamReader(socket.getInputStream()))
                        val writer = BufferedWriter(OutputStreamWriter(socket.getOutputStream()))

                        while (running && !socket.isClosed) {
                            try {
                                val line = reader.readLine() ?: break
                                when {
                                    line.equals("PING", true) || line.equals("HEARTBEAT", true) -> {
                                        writer.write("PONG\n")
                                        writer.flush()
                                    }
                                    line.equals("STATUS", true) -> {
                                        writer.write(buildStatusLine() + "\n")
                                        writer.flush()
                                    }
                                    line.equals("STOP", true) || line.equals("OFF", true) -> {
                                        glyph.off()
                                        lastFrameMs = 0L
                                    }
                                    line.startsWith("FRAME:", true) -> handleFrame(line)
                                }
                            } catch (_: SocketTimeoutException) {
                                // Socket is alive; keep waiting for the PC.
                            }
                        }
                    } catch (e: Exception) {
                        Log.w(TAG, "PC disconnected", e)
                    } finally {
                        glyph.off()
                        lastFrameMs = 0L
                        setServiceState(connected = false, label = "")
                        try { socket.close() } catch (_: Exception) {}
                        clientSocket = null
                    }
                }
            } catch (e: Exception) {
                if (running) Log.e(TAG, "TCP server stopped", e)
            }
        }
    }

    private fun handleFrame(msg: String) {
        val values = msg.substringAfter(":")
            .split(",")
            .mapNotNull { it.trim().toIntOrNull() }

        if (values.size == 5) {
            glyph.setFrame(values.toIntArray())
            lastFrameMs = System.currentTimeMillis()
        }
    }

    private fun buildStatusLine(): String {
        val batteryIntent = registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val level = batteryIntent?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
        val scale = batteryIntent?.getIntExtra(BatteryManager.EXTRA_SCALE, 100) ?: 100
        val percent = if (level >= 0 && scale > 0) level * 100 / scale else -1

        val status = batteryIntent?.getIntExtra(
            BatteryManager.EXTRA_STATUS,
            BatteryManager.BATTERY_STATUS_UNKNOWN
        ) ?: BatteryManager.BATTERY_STATUS_UNKNOWN
        val charging = status == BatteryManager.BATTERY_STATUS_CHARGING ||
            status == BatteryManager.BATTERY_STATUS_FULL

        val plugged = batteryIntent?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0
        val plugName = when (plugged) {
            BatteryManager.BATTERY_PLUGGED_USB -> "USB"
            BatteryManager.BATTERY_PLUGGED_AC -> "AC"
            BatteryManager.BATTERY_PLUGGED_WIRELESS -> "Wireless"
            else -> "Battery"
        }

        return "STATUS|battery=$percent|charging=${if (charging) 1 else 0}|plug=$plugName"
    }

    private fun startWatchdog() {
        thread(name = "GlyphLink-Watchdog") {
            while (running) {
                val last = lastFrameMs
                if (last > 0L && System.currentTimeMillis() - last > WATCHDOG_MS) {
                    glyph.off()
                    lastFrameMs = 0L
                }
                try { Thread.sleep(100) } catch (_: InterruptedException) {}
            }
        }
    }

    override fun onDestroy() {
        running = false
        lastFrameMs = 0L
        try { glyph.off() } catch (_: Exception) {}
        try { clientSocket?.close() } catch (_: Exception) {}
        try { serverSocket?.close() } catch (_: Exception) {}
        try { glyph.close() } catch (_: Exception) {}
        setServiceState(running = false, connected = false, label = "")
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
