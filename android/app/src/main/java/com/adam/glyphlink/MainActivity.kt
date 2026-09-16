package com.adam.glyphlink

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.BatteryManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

class MainActivity : Activity() {
    private lateinit var connectionTitle: TextView
    private lateinit var connectionSub: TextView
    private lateinit var connectionDot: TextView
    private lateinit var batteryValue: TextView
    private lateinit var batterySub: TextView
    private val handler = Handler(Looper.getMainLooper())

    private val updater = object : Runnable {
        override fun run() {
            refreshStatus()
            handler.postDelayed(this, 700)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.statusBarColor = Color.rgb(8, 8, 8)
        window.navigationBarColor = Color.rgb(8, 8, 8)

        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 100)
        }

        startForegroundService(Intent(this, GlyphLinkService::class.java))
        setContentView(buildUi())
    }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()

    private fun rounded(fill: Int, radiusDp: Int = 24, stroke: Int? = null): GradientDrawable =
        GradientDrawable().apply {
            shape = GradientDrawable.RECTANGLE
            setColor(fill)
            cornerRadius = dp(radiusDp).toFloat()
            if (stroke != null) setStroke(dp(1), stroke)
        }

    private fun text(value: String, size: Float, color: Int, bold: Boolean = false): TextView =
        TextView(this).apply {
            text = value
            textSize = size
            setTextColor(color)
            typeface = if (bold) Typeface.create("sans-serif", Typeface.BOLD)
            else Typeface.create("sans-serif", Typeface.NORMAL)
            includeFontPadding = false
        }

    private fun card(): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(dp(20), dp(20), dp(20), dp(20))
        background = rounded(Color.rgb(20, 20, 20), 24, Color.rgb(40, 40, 40))
    }

    private fun buildUi(): View {
        val root = ScrollView(this).apply {
            setBackgroundColor(Color.rgb(8, 8, 8))
            isFillViewport = true
        }
        val content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(22), dp(32), dp(22), dp(28))
        }

        content.addView(text("GlyphLink", 34f, Color.WHITE, true))
        content.addView(text("Nothing Phone (1) • USB companion", 14f, Color.rgb(150, 150, 150)).apply {
            setPadding(0, dp(6), 0, dp(26))
        })

        val connectionCard = card()
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        connectionDot = text("●", 18f, Color.WHITE, true)
        val wrap = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(12), 0, 0, 0)
        }
        connectionTitle = text("Waiting for USB", 20f, Color.WHITE, true)
        connectionSub = text("GlyphLink is running in the background", 13f, Color.rgb(150, 150, 150)).apply {
            setPadding(0, dp(5), 0, 0)
        }
        wrap.addView(connectionTitle)
        wrap.addView(connectionSub)
        row.addView(connectionDot, LinearLayout.LayoutParams(dp(28), dp(28)))
        row.addView(wrap, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        connectionCard.addView(row)
        content.addView(connectionCard, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            setMargins(0, 0, 0, dp(14))
        })

        val batteryCard = card()
        batteryCard.addView(text("PHONE BATTERY", 11f, Color.rgb(130, 130, 130), true))
        batteryValue = text("--%", 32f, Color.WHITE, true).apply { setPadding(0, dp(10), 0, 0) }
        batterySub = text("Checking battery…", 13f, Color.rgb(155, 155, 155)).apply { setPadding(0, dp(6), 0, 0) }
        batteryCard.addView(batteryValue)
        batteryCard.addView(batterySub)
        content.addView(batteryCard, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            setMargins(0, 0, 0, dp(14))
        })

        val always = card()
        always.addView(text("Always ready", 18f, Color.WHITE, true))
        always.addView(text(
            "No buttons needed. GlyphLink stays ready in the background and automatically clears the Glyphs if the PC disconnects.",
            13f,
            Color.rgb(160, 160, 160)
        ).apply {
            setPadding(0, dp(8), 0, 0)
            setLineSpacing(0f, 1.15f)
        })
        content.addView(always)

        content.addView(text("GLYPHLINK  •  1.3.4", 11f, Color.rgb(90, 90, 90), true).apply {
            gravity = Gravity.CENTER
            setPadding(0, dp(28), 0, 0)
        }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))

        root.addView(content)
        return root
    }

    private fun refreshStatus() {
        val prefs = getSharedPreferences(GlyphLinkService.PREFS, MODE_PRIVATE)
        val running = prefs.getBoolean(GlyphLinkService.KEY_RUNNING, false)
        val connected = prefs.getBoolean(GlyphLinkService.KEY_CONNECTED, false)

        when {
            connected -> {
                connectionDot.setTextColor(Color.rgb(103, 232, 141))
                connectionTitle.text = "PC connected"
                connectionSub.text = "Stable USB debugging bridge active"
            }
            running -> {
                connectionDot.setTextColor(Color.WHITE)
                connectionTitle.text = "Waiting for USB"
                connectionSub.text = "Service is ready — connect your PC when you want"
            }
            else -> {
                connectionDot.setTextColor(Color.rgb(255, 190, 90))
                connectionTitle.text = "Starting GlyphLink…"
                connectionSub.text = "The background service is restarting"
                startForegroundService(Intent(this, GlyphLinkService::class.java))
            }
        }

        val batteryIntent = registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val level = batteryIntent?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
        val scale = batteryIntent?.getIntExtra(BatteryManager.EXTRA_SCALE, 100) ?: 100
        val percent = if (level >= 0 && scale > 0) level * 100 / scale else -1
        val status = batteryIntent?.getIntExtra(BatteryManager.EXTRA_STATUS, BatteryManager.BATTERY_STATUS_UNKNOWN)
            ?: BatteryManager.BATTERY_STATUS_UNKNOWN
        val charging = status == BatteryManager.BATTERY_STATUS_CHARGING || status == BatteryManager.BATTERY_STATUS_FULL
        val plugged = batteryIntent?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0
        val source = when (plugged) {
            BatteryManager.BATTERY_PLUGGED_USB -> "USB"
            BatteryManager.BATTERY_PLUGGED_AC -> "AC charger"
            BatteryManager.BATTERY_PLUGGED_WIRELESS -> "Wireless charger"
            else -> "On battery"
        }

        batteryValue.text = if (percent >= 0) "$percent%" else "--%"
        batterySub.text = if (charging) "Charging • $source" else source
    }

    override fun onResume() {
        super.onResume()
        handler.post(updater)
    }

    override fun onPause() {
        handler.removeCallbacks(updater)
        super.onPause()
    }
}
