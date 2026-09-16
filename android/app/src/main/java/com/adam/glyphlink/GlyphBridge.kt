package com.adam.glyphlink

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.os.IBinder
import android.util.Log
import com.nothing.thirdparty.IGlyphService

class GlyphBridge(private val context: Context) {
    companion object {
        private const val TAG = "GlyphBridge"
    }

    @Volatile
    private var service: IGlyphService? = null

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            service = IGlyphService.Stub.asInterface(binder)
            try {
                // Evolution X's adapter currently returns true for both calls,
                // but keeping these here makes the handshake explicit.
                service?.register("GlyphLink")
                service?.registerSDK("DEVICE_20111", context.packageName)
                service?.openSession()
                Log.i(TAG, "Connected to Evolution X GlyphAdapter")
            } catch (e: Exception) {
                Log.e(TAG, "Glyph init failed", e)
            }
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            service = null
            Log.w(TAG, "Glyph service disconnected")
        }
    }

    fun connect(): Boolean {
        val intent = Intent("com.nothing.thirdparty.IGlyphService").apply {
            component = ComponentName(
                "com.nothing.thirdparty",
                "com.nothing.thirdparty.GlyphService"
            )
        }
        return context.bindService(intent, connection, Context.BIND_AUTO_CREATE)
    }

    fun setFrame(values: IntArray) {
        if (values.size != 5) return
        val clean = IntArray(5) { i -> values[i].coerceIn(0, 4095) }
        try {
            service?.setFrameColors(clean)
        } catch (e: Exception) {
            Log.e(TAG, "setFrame failed", e)
        }
    }

    fun off() = setFrame(intArrayOf(0, 0, 0, 0, 0))

    fun close() {
        try {
            service?.closeSession()
        } catch (_: Exception) {
        }
        try {
            context.unbindService(connection)
        } catch (_: Exception) {
        }
        service = null
    }
}
