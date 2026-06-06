package com.example.niftytrader

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.animation.*
import androidx.compose.animation.core.*
import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.blur
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.*
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipRect
import androidx.compose.ui.graphics.drawscope.clipPath
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL
import android.content.Context
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation

// =============================================================================
// COLOR PALETTE (EXACT WEBSITE MATCH)
// =============================================================================

val ColorBG = Color(0xFF0A0E27)
val ColorCardBG = Color(0xCC1E283C)      // rgba(30, 40, 60, 0.8)
val ColorBorder = Color(0x336496FF)      // rgba(100, 150, 255, 0.2)
val ColorGreen = Color(0xFF00FF88)       // Neon Green
val ColorRed = Color(0xFFFF4466)         // Cyberpunk Red/Pink (#ff4466)
val ColorBlue = Color(0xFF00CCFF)        // Tech Cyan (#00ccff)
val ColorGold = Color(0xFFFFD700)        // Gold (#ffd700)
val ColorMuted = Color(0xFFA0AEC0)       // Muted Text (#a0aec0)
val ColorText = Color(0xFFE8EAF6)        // Light text (#e8eaf6)
val ColorAccent = Color(0xFFBB66FF)      // Purple (#bb66ff)


// =============================================================================
// DATA SCHEMAS
// =============================================================================

data class Trade(
    val tid: String,
    val date: String,
    val entryTime: String,
    val exitTime: String,
    val direction: String,
    val strike: String,
    val opt: String,
    val entry: Double,
    val exit: Double,
    val pnl: Double,
    val reason: String,
    val charges: Double
)

data class Position(
    val tid: String,
    val index: String,
    val direction: String,
    val strike: Double,
    val opt: String,
    val contracts: Int,
    val entry: Double,
    val sl: Double,
    val tp: Double,
    val cur: Double,
    val pnl: Double,
    val pnlPct: Double,
    val charges: Double,
    val entryTime: String,
    var trailingSlEnabled: Boolean
)

data class Stats(
    val capital: Double = 0.0,
    val baseCapital: Double = 0.0,
    val totalPnl: Double = 0.0,
    val totalTrades: Int = 0,
    val wins: Int = 0,
    val losses: Int = 0,
    val winRate: Double = 0.0,
    val avgWin: Double = 0.0,
    val avgLoss: Double = 0.0,
    val rr: Double = 0.0,
    val best: Double = 0.0,
    val worst: Double = 0.0,
    val totalCharges: Double = 0.0
)

data class SmartStatus(
    val enabled: Boolean = false,
    val status: String = "✏️ LEARNING MODE",
    val accuracy: String = "—",
    val totalSamples: Int = 0,
    val wins: Int = 0,
    val losses: Int = 0,
    val filteredCount: Int = 0,
    val threshold: String = "50%"
)

data class TradingData(
    val niftySpot: Double = 0.0,
    val sensexSpot: Double = 0.0,
    val vix: Double = 15.0,
    val trades: List<Trade> = emptyList(),
    val openPositions: List<Position> = emptyList(),
    val stats: Stats = Stats(),
    val running: Boolean = false,
    val liveTrading: Boolean = false,
    val activeBroker: String = "GROWW",
    val smartFilterEnabled: Boolean = false,
    val trailingSlEnabled: Boolean = false,
    val dhanClientId: String = "",
    val dhanHasToken: Boolean = false,
    val growwClientId: String = "",
    val growwHasToken: Boolean = false,
    val equity: List<Double> = emptyList(),
    val tradingIndices: List<String> = emptyList(),
    val smartStatus: SmartStatus = SmartStatus(),
    val log: List<String> = emptyList()
)

// =============================================================================
// MAIN ACTIVITY
// =============================================================================

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            NiftyTraderApp()
        }
    }
}

// =============================================================================
// MAIN APP COMPOSABLE
// =============================================================================

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun NiftyTraderApp() {
    val context = LocalContext.current
    val sharedPrefs = remember { context.getSharedPreferences("nifty_trader_prefs", android.content.Context.MODE_PRIVATE) }

    var rawBaseUrl by remember {
        mutableStateOf(sharedPrefs.getString("raw_base_url", "https://overexert-purposely-illusion.ngrok-free.dev") ?: "https://overexert-purposely-illusion.ngrok-free.dev")
    } 
    var username by remember {
        mutableStateOf(sharedPrefs.getString("username", "") ?: "")
    }
    var isAuthenticated by remember {
        mutableStateOf(sharedPrefs.getBoolean("is_authenticated", false))
    }
    val onAuthSuccess = {
        isAuthenticated = true
        sharedPrefs.edit().apply {
            putString("raw_base_url", rawBaseUrl)
            putString("username", username)
            putBoolean("is_authenticated", true)
            apply()
        }
    }

    val baseUrl = if (username.isNotEmpty()) "$rawBaseUrl?username=$username" else rawBaseUrl
    var isPolling by remember { mutableStateOf(true) }
    var tradingData by remember { mutableStateOf(TradingData()) }
    var connectionError by remember { mutableStateOf<String?>(null) }
    var showUrlConfig by remember { mutableStateOf(false) }
    val coroutineScope = rememberCoroutineScope()

    // Live Broker Authentication State
    var showAuthDialog by remember { mutableStateOf(false) }
    var authBroker by remember { mutableStateOf("GROWW") }
    var authClientId by remember { mutableStateOf("") }
    var authAccessToken by remember { mutableStateOf("") }
    var authReuseSavedToken by remember { mutableStateOf(false) }
    var authErrorMessage by remember { mutableStateOf<String?>(null) }
    var authIsSubmitting by remember { mutableStateOf(false) }
    var onAuthSuccessAction by remember { mutableStateOf<(() -> Unit)?>(null) }

    // Manual Edit Position Overlay Dialog
    var editingPosition by remember { mutableStateOf<Position?>(null) }
    var editSl by remember { mutableStateOf("") }
    var editTp by remember { mutableStateOf("") }
    var editTsl by remember { mutableStateOf(false) }
    var isUpdatingPosition by remember { mutableStateOf(false) }

    var showFullHistory by remember { mutableStateOf(false) }
    var isRefreshing by remember { mutableStateOf(false) }
    val infiniteTransition = rememberInfiniteTransition(label = "refresh_spin")
    val spinAngle by infiniteTransition.animateFloat(
        initialValue = 0f,
        targetValue = 360f,
        animationSpec = infiniteRepeatable(tween(800, easing = LinearEasing)),
        label = "spin_angle"
    )

    // Start auto-polling coroutine
    LaunchedEffect(baseUrl, isPolling, isAuthenticated) {
        while (isPolling && isAuthenticated) {
            try {
                val data = fetchDashboardData(baseUrl)
                tradingData = data
                connectionError = null
            } catch (e: Exception) {
                connectionError = e.message ?: "Failed to connect to backend server"
            }
            delay(5000)
        }
    }

    if (!isAuthenticated) {
        AuthScreen(
            rawBaseUrl = rawBaseUrl,
            onUrlChange = { 
                rawBaseUrl = it 
                sharedPrefs.edit().putString("raw_base_url", it).apply()
            },
            username = username,
            onUsernameChange = { 
                username = it 
                sharedPrefs.edit().putString("username", it).apply()
            },
            onAuthSuccess = {
                onAuthSuccess()
            }
        )
    } else {
        Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Logo3D(modifier = Modifier.size(24.dp))
                        Spacer(modifier = Modifier.width(8.dp))
                        Text(
                            text = "ALGO PULSE",
                            style = MaterialTheme.typography.titleMedium.copy(
                                fontWeight = FontWeight.Black,
                                color = Color.White,
                                fontFamily = FontFamily.Monospace,
                                letterSpacing = 1.sp
                            )
                        )
                        Spacer(modifier = Modifier.width(8.dp))
                        Box(
                            modifier = Modifier
                                .size(8.dp)
                                .clip(RoundedCornerShape(4.dp))
                                .background(if (tradingData.running) ColorGreen else ColorRed)
                        )
                    }
                },
                actions = {
                    // Refresh button
                    IconButton(
                        onClick = {
                            if (!isRefreshing) {
                                isRefreshing = true
                                coroutineScope.launch {
                                    try {
                                        refreshVixOnServer(baseUrl)
                                        tradingData = fetchDashboardData(baseUrl)
                                        connectionError = null
                                    } catch (e: Exception) {
                                        connectionError = e.message ?: "Refresh failed"
                                    } finally {
                                        isRefreshing = false
                                    }
                                }
                            }
                        }
                    ) {
                        Text(
                            text = "⟳",
                            color = if (isRefreshing) ColorGreen else ColorMuted,
                            fontSize = 22.sp,
                            fontWeight = FontWeight.Bold,
                            modifier = Modifier.graphicsLayer(rotationZ = if (isRefreshing) spinAngle else 0f)
                        )
                    }
                    // Settings button
                    IconButton(onClick = { showUrlConfig = !showUrlConfig }) {
                        Text(
                            text = "⚙",
                            color = ColorBlue,
                            fontSize = 24.sp,
                            fontWeight = FontWeight.Bold
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = Color(0xFF090D1A),
                    titleContentColor = Color.White
                )
            )
        },
        containerColor = ColorBG
    ) { innerPadding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(ColorBG)
                .padding(innerPadding)
        ) {
            // Ambient light radial glow simulation
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .background(
                        Brush.radialGradient(
                            colors = listOf(Color(0x1F9D4EDD), Color.Transparent),
                            radius = 400f
                        )
                    )
            )

            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .blur(if (showFullHistory) 12.dp else 0.dp)
                    .verticalScroll(rememberScrollState())
                    .padding(horizontal = 16.dp, vertical = 8.dp)
            ) {
                // Expandable Settings & Control Panel configuration
                AnimatedVisibility(visible = showUrlConfig) {
                    Card(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(bottom = 16.dp),
                        shape = RoundedCornerShape(12.dp),
                        colors = CardDefaults.cardColors(containerColor = ColorCardBG),
                        border = BorderStroke(1.dp, ColorBlue.copy(alpha = 0.3f))
                    ) {
                        Column(modifier = Modifier.padding(16.dp)) {
                            Text(
                                text = "⚙️ SYSTEM CONTROL PANEL",
                                style = MaterialTheme.typography.labelSmall,
                                color = ColorBlue,
                                fontWeight = FontWeight.Bold,
                                fontFamily = FontFamily.Monospace
                            )
                            Spacer(modifier = Modifier.height(12.dp))

                            Button(
                                onClick = {
                                    isAuthenticated = false
                                    sharedPrefs.edit().apply {
                                        putBoolean("is_authenticated", false)
                                        apply()
                                    }
                                    coroutineScope.launch {
                                        try {
                                            val url = buildUrl(baseUrl, "/api/logout")
                                            val conn = url.openConnection() as HttpURLConnection
                                            conn.requestMethod = "POST"
                                            conn.connectTimeout = 3000
                                            conn.inputStream?.close()
                                            conn.disconnect()
                                        } catch (e: Exception) {}
                                    }
                                },
                                modifier = Modifier.fillMaxWidth().height(36.dp),
                                shape = RoundedCornerShape(8.dp),
                                colors = ButtonDefaults.buttonColors(containerColor = ColorRed.copy(alpha = 0.2f)),
                                border = BorderStroke(1.dp, ColorRed)
                            ) {
                                Text(
                                    text = "🔒 LOG OUT SESSION",
                                    color = ColorRed,
                                    fontSize = 10.sp,
                                    fontWeight = FontWeight.Bold,
                                    fontFamily = FontFamily.Monospace
                                )
                            }
                            
                            Spacer(modifier = Modifier.height(16.dp))
                            Box(modifier = Modifier.fillMaxWidth().height(1.dp).background(ColorBorder))
                            Spacer(modifier = Modifier.height(16.dp))

                            // 1. Bot state control (Start Bot / Stop Bot)
                            Text(
                                text = "🤖 ALGO TRADING ENGINE",
                                style = MaterialTheme.typography.labelSmall,
                                color = ColorBlue,
                                fontWeight = FontWeight.Bold,
                                fontFamily = FontFamily.Monospace
                            )
                            Spacer(modifier = Modifier.height(8.dp))
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.SpaceBetween
                            ) {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Box(
                                        modifier = Modifier
                                            .size(8.dp)
                                            .clip(RoundedCornerShape(4.dp))
                                            .background(if (tradingData.running) ColorGreen else ColorRed)
                                    )
                                    Spacer(modifier = Modifier.width(8.dp))
                                    Text(
                                        text = if (tradingData.running) "ACTIVE & RUNNING" else "STOPPED / INACTIVE",
                                        color = ColorText,
                                        fontSize = 11.sp,
                                        fontWeight = FontWeight.Bold,
                                        fontFamily = FontFamily.Monospace
                                    )
                                }

                                var isSubmittingBot by remember { mutableStateOf(false) }
                                var isSquaringOff by remember { mutableStateOf(false) }
                                
                                Row(
                                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                                    verticalAlignment = Alignment.CenterVertically
                                ) {
                                    Button(
                                        onClick = {
                                            if (!isSubmittingBot) {
                                                isSubmittingBot = true
                                                coroutineScope.launch {
                                                    controlBot(baseUrl, !tradingData.running)
                                                    delay(500)
                                                    try {
                                                        tradingData = fetchDashboardData(baseUrl)
                                                    } catch (e: Exception) {}
                                                     isSubmittingBot = false
                                                }
                                            }
                                        },
                                        modifier = Modifier.height(28.dp),
                                        contentPadding = PaddingValues(horizontal = 10.dp, vertical = 0.dp),
                                        shape = RoundedCornerShape(6.dp),
                                        colors = ButtonDefaults.buttonColors(
                                            containerColor = if (tradingData.running) ColorRed.copy(alpha = 0.2f) else ColorGreen.copy(alpha = 0.2f)
                                        ),
                                        border = BorderStroke(1.dp, if (tradingData.running) ColorRed else ColorGreen),
                                        enabled = !isSubmittingBot
                                    ) {
                                        Text(
                                            text = if (tradingData.running) "🛑 STOP BOT" else "▶️ START BOT",
                                            color = if (tradingData.running) ColorRed else ColorGreen,
                                            fontSize = 8.5.sp,
                                            fontWeight = FontWeight.Black,
                                            fontFamily = FontFamily.Monospace
                                        )
                                    }

                                    Button(
                                        onClick = {
                                            if (!isSquaringOff) {
                                                isSquaringOff = true
                                                coroutineScope.launch {
                                                    squareOffAllPositions(baseUrl)
                                                    delay(500)
                                                    try {
                                                        tradingData = fetchDashboardData(baseUrl)
                                                    } catch (e: Exception) {}
                                                    isSquaringOff = false
                                                }
                                            }
                                        },
                                        modifier = Modifier.height(28.dp),
                                        contentPadding = PaddingValues(horizontal = 10.dp, vertical = 0.dp),
                                        shape = RoundedCornerShape(6.dp),
                                        colors = ButtonDefaults.buttonColors(
                                            containerColor = ColorGold.copy(alpha = 0.2f)
                                        ),
                                        border = BorderStroke(1.dp, ColorGold),
                                        enabled = !isSquaringOff
                                    ) {
                                        Text(
                                            text = "⚡ SQUARE OFF",
                                            color = ColorGold,
                                            fontSize = 8.5.sp,
                                            fontWeight = FontWeight.Black,
                                            fontFamily = FontFamily.Monospace
                                        )
                                    }
                                }
                            }

                            Spacer(modifier = Modifier.height(16.dp))
                            Box(modifier = Modifier.fillMaxWidth().height(1.dp).background(ColorBorder))
                            Spacer(modifier = Modifier.height(16.dp))

                            // 2. Mode selection (Paper / Live)
                            Text(
                                text = "⚡ EXECUTION MODE",
                                style = MaterialTheme.typography.labelSmall,
                                color = ColorBlue,
                                fontWeight = FontWeight.Bold,
                                fontFamily = FontFamily.Monospace
                            )
                            Spacer(modifier = Modifier.height(8.dp))
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.spacedBy(8.dp)
                            ) {
                                var isSubmittingMode by remember { mutableStateOf(false) }
                                // Paper Mode Button
                                Button(
                                    onClick = {
                                        if (!isSubmittingMode && tradingData.liveTrading) {
                                            isSubmittingMode = true
                                            coroutineScope.launch {
                                                setTradingMode(baseUrl, false)
                                                delay(500)
                                                try {
                                                    tradingData = fetchDashboardData(baseUrl)
                                                } catch (e: Exception) {}
                                                isSubmittingMode = false
                                            }
                                        }
                                    },
                                    modifier = Modifier.weight(1f).height(32.dp),
                                    contentPadding = PaddingValues(0.dp),
                                    shape = RoundedCornerShape(6.dp),
                                    colors = ButtonDefaults.buttonColors(
                                        containerColor = if (!tradingData.liveTrading) ColorBlue.copy(alpha = 0.15f) else Color.Transparent
                                    ),
                                    border = BorderStroke(1.dp, if (!tradingData.liveTrading) ColorBlue else ColorBorder)
                                ) {
                                    Text(
                                        text = "📈 DEMO (PAPER)",
                                        color = if (!tradingData.liveTrading) ColorBlue else ColorMuted,
                                        fontSize = 9.sp,
                                        fontWeight = FontWeight.Bold,
                                        fontFamily = FontFamily.Monospace
                                    )
                                }

                                // Live Mode Button
                                Button(
                                    onClick = {
                                        if (!isSubmittingMode && !tradingData.liveTrading) {
                                            authBroker = tradingData.activeBroker
                                            authClientId = if (authBroker == "DHAN") tradingData.dhanClientId else tradingData.growwClientId
                                            authAccessToken = ""
                                            authReuseSavedToken = if (authBroker == "DHAN") tradingData.dhanHasToken else tradingData.growwHasToken
                                            authErrorMessage = null
                                            onAuthSuccessAction = {
                                                coroutineScope.launch {
                                                    setTradingMode(baseUrl, true)
                                                    delay(500)
                                                    try {
                                                        tradingData = fetchDashboardData(baseUrl)
                                                    } catch (e: Exception) {}
                                                }
                                            }
                                            showAuthDialog = true
                                        }
                                    },
                                    modifier = Modifier.weight(1f).height(32.dp),
                                    contentPadding = PaddingValues(0.dp),
                                    shape = RoundedCornerShape(6.dp),
                                    colors = ButtonDefaults.buttonColors(
                                        containerColor = if (tradingData.liveTrading) ColorRed.copy(alpha = 0.15f) else Color.Transparent
                                    ),
                                    border = BorderStroke(1.dp, if (tradingData.liveTrading) ColorRed else ColorBorder)
                                ) {
                                    Text(
                                        text = "⚠️ LIVE TRADING",
                                        color = if (tradingData.liveTrading) ColorRed else ColorMuted,
                                        fontSize = 9.sp,
                                        fontWeight = FontWeight.Bold,
                                        fontFamily = FontFamily.Monospace
                                    )
                                }
                            }

                            Spacer(modifier = Modifier.height(16.dp))
                            Box(modifier = Modifier.fillMaxWidth().height(1.dp).background(ColorBorder))
                            Spacer(modifier = Modifier.height(16.dp))

                            // 3. Connected Broker selector
                            Text(
                                text = "🔌 CONNECT TO BROKER",
                                style = MaterialTheme.typography.labelSmall,
                                color = ColorBlue,
                                fontWeight = FontWeight.Bold,
                                fontFamily = FontFamily.Monospace
                            )
                            Spacer(modifier = Modifier.height(8.dp))
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.spacedBy(8.dp)
                            ) {
                                var isSubmittingBroker by remember { mutableStateOf(false) }

                                // Groww Button
                                Button(
                                    onClick = {
                                        if (!isSubmittingBroker && tradingData.activeBroker != "GROWW") {
                                            if (tradingData.liveTrading) {
                                                authBroker = "GROWW"
                                                authClientId = tradingData.growwClientId
                                                authAccessToken = ""
                                                authReuseSavedToken = tradingData.growwHasToken
                                                authErrorMessage = null
                                                onAuthSuccessAction = {
                                                    coroutineScope.launch {
                                                        setActiveBroker(baseUrl, "GROWW")
                                                        delay(500)
                                                        try {
                                                            tradingData = fetchDashboardData(baseUrl)
                                                        } catch (e: Exception) {}
                                                    }
                                                }
                                                showAuthDialog = true
                                            } else {
                                                isSubmittingBroker = true
                                                coroutineScope.launch {
                                                    setActiveBroker(baseUrl, "GROWW")
                                                    delay(500)
                                                    try {
                                                        tradingData = fetchDashboardData(baseUrl)
                                                    } catch (e: Exception) {}
                                                    isSubmittingBroker = false
                                                }
                                            }
                                        }
                                    },
                                    modifier = Modifier.fillMaxWidth().height(32.dp),
                                    contentPadding = PaddingValues(0.dp),
                                    shape = RoundedCornerShape(6.dp),
                                    colors = ButtonDefaults.buttonColors(
                                        containerColor = if (tradingData.activeBroker == "GROWW") ColorBlue.copy(alpha = 0.15f) else Color.Transparent
                                    ),
                                    border = BorderStroke(1.dp, if (tradingData.activeBroker == "GROWW") ColorBlue else ColorBorder)
                                ) {
                                    Text(
                                        text = "🌱 GROWW BROKER",
                                        color = if (tradingData.activeBroker == "GROWW") ColorBlue else ColorMuted,
                                        fontSize = 9.sp,
                                        fontWeight = FontWeight.Bold,
                                        fontFamily = FontFamily.Monospace
                                    )
                                }
                            }

                            Spacer(modifier = Modifier.height(16.dp))
                            Box(modifier = Modifier.fillMaxWidth().height(1.dp).background(ColorBorder))
                            Spacer(modifier = Modifier.height(16.dp))

                            // 4. Smart guard and trailing SL toggles
                            Text(
                                text = "🛡️ RISK & FILTER CONTROLS",
                                style = MaterialTheme.typography.labelSmall,
                                color = ColorBlue,
                                fontWeight = FontWeight.Bold,
                                fontFamily = FontFamily.Monospace
                            )
                            Spacer(modifier = Modifier.height(8.dp))
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.spacedBy(8.dp)
                            ) {
                                var isSubmittingFilter by remember { mutableStateOf(false) }
                                // Smart Guard Toggle Button
                                Button(
                                    onClick = {
                                        if (!isSubmittingFilter) {
                                            isSubmittingFilter = true
                                            coroutineScope.launch {
                                                toggleSmartFilter(baseUrl, !tradingData.smartFilterEnabled)
                                                delay(500)
                                                try {
                                                    tradingData = fetchDashboardData(baseUrl)
                                                } catch (e: Exception) {}
                                                isSubmittingFilter = false
                                            }
                                        }
                                    },
                                    modifier = Modifier.weight(1f).height(32.dp),
                                    contentPadding = PaddingValues(0.dp),
                                    shape = RoundedCornerShape(6.dp),
                                    colors = ButtonDefaults.buttonColors(
                                        containerColor = if (tradingData.smartFilterEnabled) ColorGreen.copy(alpha = 0.15f) else Color.Transparent
                                    ),
                                    border = BorderStroke(1.dp, if (tradingData.smartFilterEnabled) ColorGreen else ColorBorder)
                                ) {
                                    Text(
                                        text = if (tradingData.smartFilterEnabled) "🛡️ GUARD: ON" else "🛡️ GUARD: OFF",
                                        color = if (tradingData.smartFilterEnabled) ColorGreen else ColorMuted,
                                        fontSize = 9.sp,
                                        fontWeight = FontWeight.Bold,
                                        fontFamily = FontFamily.Monospace
                                    )
                                }

                                // Trailing SL Toggle Button
                                Button(
                                    onClick = {
                                        if (!isSubmittingFilter) {
                                            isSubmittingFilter = true
                                            coroutineScope.launch {
                                                toggleTrailingSl(baseUrl, !tradingData.trailingSlEnabled)
                                                delay(500)
                                                try {
                                                    tradingData = fetchDashboardData(baseUrl)
                                                } catch (e: Exception) {}
                                                isSubmittingFilter = false
                                            }
                                        }
                                    },
                                    modifier = Modifier.weight(1f).height(32.dp),
                                    contentPadding = PaddingValues(0.dp),
                                    shape = RoundedCornerShape(6.dp),
                                    colors = ButtonDefaults.buttonColors(
                                        containerColor = if (tradingData.trailingSlEnabled) ColorGreen.copy(alpha = 0.15f) else Color.Transparent
                                    ),
                                    border = BorderStroke(1.dp, if (tradingData.trailingSlEnabled) ColorGreen else ColorBorder)
                                ) {
                                    Text(
                                        text = if (tradingData.trailingSlEnabled) "📈 TSL: ON" else "📈 TSL: OFF",
                                        color = if (tradingData.trailingSlEnabled) ColorGreen else ColorMuted,
                                        fontSize = 9.sp,
                                        fontWeight = FontWeight.Bold,
                                        fontFamily = FontFamily.Monospace
                                    )
                                }
                            }

                            Spacer(modifier = Modifier.height(16.dp))
                            Box(modifier = Modifier.fillMaxWidth().height(1.dp).background(ColorBorder))
                            Spacer(modifier = Modifier.height(16.dp))

                            // 5. Active trading indices selection
                            Text(
                                text = "📈 ACTIVE TRADING INDICES",
                                style = MaterialTheme.typography.labelSmall,
                                color = ColorBlue,
                                fontWeight = FontWeight.Bold,
                                fontFamily = FontFamily.Monospace
                            )
                            Spacer(modifier = Modifier.height(12.dp))

                            Column(
                                modifier = Modifier.fillMaxWidth(),
                                verticalArrangement = Arrangement.spacedBy(8.dp)
                            ) {
                                val allIndices = listOf("NIFTY", "SENSEX", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "BANKEX")
                                
                                for (i in allIndices.indices step 2) {
                                    Row(
                                        modifier = Modifier.fillMaxWidth(),
                                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                                    ) {
                                        for (j in 0..1) {
                                            if (i + j < allIndices.size) {
                                                val indexName = allIndices[i + j]
                                                val isChecked = tradingData.tradingIndices.contains(indexName)
                                                
                                                var isUpdatingIndex by remember { mutableStateOf(false) }
                                                
                                                Button(
                                                    onClick = {
                                                        if (!isUpdatingIndex) {
                                                            isUpdatingIndex = true
                                                            val newList = if (isChecked) {
                                                                tradingData.tradingIndices.filter { it != indexName }
                                                            } else {
                                                                tradingData.tradingIndices + indexName
                                                            }
                                                            coroutineScope.launch {
                                                                updateTradingIndices(baseUrl, newList)
                                                                delay(500)
                                                                try {
                                                                    tradingData = fetchDashboardData(baseUrl)
                                                                } catch (e: Exception) {}
                                                                isUpdatingIndex = false
                                                            }
                                                        }
                                                    },
                                                    modifier = Modifier.weight(1f).height(36.dp),
                                                    contentPadding = PaddingValues(horizontal = 8.dp, vertical = 0.dp),
                                                    shape = RoundedCornerShape(8.dp),
                                                    colors = ButtonDefaults.buttonColors(
                                                        containerColor = if (isChecked) ColorBlue.copy(alpha = 0.15f) else Color.Transparent
                                                    ),
                                                    border = BorderStroke(
                                                        1.dp,
                                                        if (isChecked) ColorBlue else ColorBorder
                                                    ),
                                                    enabled = !isUpdatingIndex
                                                ) {
                                                    Row(
                                                        verticalAlignment = Alignment.CenterVertically,
                                                        horizontalArrangement = Arrangement.Center
                                                    ) {
                                                        Box(
                                                            modifier = Modifier
                                                                .size(6.dp)
                                                                .clip(RoundedCornerShape(3.dp))
                                                                .background(if (isChecked) ColorGreen else ColorRed)
                                                        )
                                                        Spacer(modifier = Modifier.width(8.dp))
                                                        Text(
                                                            text = indexName,
                                                            color = if (isChecked) ColorText else ColorMuted,
                                                            fontSize = 10.sp,
                                                            fontWeight = FontWeight.Bold,
                                                            fontFamily = FontFamily.Monospace
                                                        )
                                                    }
                                                }
                                            } else {
                                                Spacer(modifier = Modifier.weight(1f))
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                // Error indicator
                if (connectionError != null) {
                    Card(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(bottom = 16.dp)
                            .clickable { showUrlConfig = true },
                        shape = RoundedCornerShape(8.dp),
                        colors = CardDefaults.cardColors(containerColor = ColorRed.copy(alpha = 0.15f)),
                        border = BorderStroke(1.dp, ColorRed.copy(alpha = 0.4f))
                    ) {
                        Column(modifier = Modifier.padding(12.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(
                                    text = "⚠",
                                    color = ColorRed,
                                    fontSize = 20.sp,
                                    fontWeight = FontWeight.Bold
                                )
                                Spacer(modifier = Modifier.width(10.dp))
                                Column {
                                    Text(
                                        text = "CONNECTION LOST",
                                        color = ColorRed,
                                        fontSize = 11.sp,
                                        fontWeight = FontWeight.Black,
                                        fontFamily = FontFamily.Monospace
                                    )
                                    Text(
                                        text = "Server: $baseUrl",
                                        style = MaterialTheme.typography.bodySmall,
                                        color = ColorMuted,
                                        fontFamily = FontFamily.Monospace,
                                        fontSize = 9.sp
                                    )
                                }
                            }
                            Spacer(modifier = Modifier.height(8.dp))
                            Text(
                                text = "📡 TAP HERE to open Settings and update the server URL  →  ⚙",
                                color = ColorBlue,
                                fontSize = 9.sp,
                                fontFamily = FontFamily.Monospace,
                                fontWeight = FontWeight.Bold
                            )
                        }
                    }
                }


                // 1. LIVE SYSTEM METRICS HEADER (WEBSITE STYLE)
                LiveIndexHeader(
                    nifty = tradingData.niftySpot,
                    sensex = tradingData.sensexSpot,
                    vix = tradingData.vix,
                    live = tradingData.liveTrading,
                    broker = tradingData.activeBroker
                )

                Spacer(modifier = Modifier.height(16.dp))

                // 2. DAY NET P&L CARDS WITH GLOW
                DayPnlCard(
                    pnl = tradingData.stats.totalPnl,
                    capital = tradingData.stats.capital,
                    baseCapital = tradingData.stats.baseCapital,
                    totalTrades = tradingData.stats.totalTrades
                )

                Spacer(modifier = Modifier.height(16.dp))

                // 3. PERFORMANCE STATS & CHART GRID
                StatsSection(tradingData.stats)

                Spacer(modifier = Modifier.height(16.dp))

                // 3.2 SMART SIGNAL GUARD CARD
                var isUpdatingFilterCard by remember { mutableStateOf(false) }
                SmartSignalGuardCard(
                    status = tradingData.smartStatus,
                    onToggleChange = { enabled ->
                        if (!isUpdatingFilterCard) {
                            isUpdatingFilterCard = true
                            coroutineScope.launch {
                                toggleSmartFilter(baseUrl, enabled)
                                delay(500)
                                try {
                                    tradingData = fetchDashboardData(baseUrl)
                                } catch (e: Exception) {}
                                isUpdatingFilterCard = false
                            }
                        }
                    }
                )

                Spacer(modifier = Modifier.height(16.dp))

                // 3.5 INTERACTIVE CHART VISUALIZATION (DROPDOWN-TOGGLED)
                InteractiveChartCard(tradingData)

                Spacer(modifier = Modifier.height(20.dp))

                // 4. ACTIVE POSITIONS
                Row(
                    modifier = Modifier.padding(bottom = 10.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        text = "⚡",
                        fontSize = 18.sp
                    )
                    Spacer(modifier = Modifier.width(6.dp))
                    Text(
                        text = "LIVE POSITIONS",
                        color = ColorText,
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Black,
                        fontFamily = FontFamily.Monospace,
                        letterSpacing = 2.sp
                    )
                    Spacer(modifier = Modifier.width(8.dp))
                    if (tradingData.openPositions.isNotEmpty()) {
                        Box(
                            modifier = Modifier
                                .clip(RoundedCornerShape(10.dp))
                                .background(
                                    Brush.horizontalGradient(
                                        listOf(Color(0xFFFF6B00), Color(0xFFFF3D71))
                                    )
                                )
                                .padding(horizontal = 8.dp, vertical = 2.dp),
                            contentAlignment = Alignment.Center
                        ) {
                            Text(
                                text = "${tradingData.openPositions.size} ACTIVE",
                                color = Color.White,
                                fontSize = 8.sp,
                                fontWeight = FontWeight.Black,
                                fontFamily = FontFamily.Monospace,
                                letterSpacing = 1.sp
                            )
                        }
                    }
                } // end Row header

                if (tradingData.openPositions.isEmpty()) {
                    Card(
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(80.dp),
                        shape = RoundedCornerShape(12.dp),
                        colors = CardDefaults.cardColors(containerColor = ColorCardBG),
                        border = BorderStroke(1.dp, ColorBorder)
                    ) {
                        Box(
                            modifier = Modifier.fillMaxSize(),
                            contentAlignment = Alignment.Center
                        ) {
                            Text(
                                text = "NO ACTIVE POSITIONS FOUND",
                                color = ColorMuted,
                                style = MaterialTheme.typography.bodyMedium,
                                fontFamily = FontFamily.Monospace
                            )
                        }
                    }
                } else {
                    tradingData.openPositions.forEach { pos ->
                        PositionCard(
                            position = pos,
                            onEditClick = {
                                editingPosition = pos
                                editSl = pos.sl.toString()
                                editTp = pos.tp.toString()
                                editTsl = pos.trailingSlEnabled
                            }
                        )
                        Spacer(modifier = Modifier.height(12.dp))
                    }
                }

                Spacer(modifier = Modifier.height(20.dp))

                // 5. TRADE LOGS / HISTORY (TAP TO EXPAND)
                Card(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable { showFullHistory = true },
                    shape = RoundedCornerShape(16.dp),
                    colors = CardDefaults.cardColors(containerColor = ColorCardBG),
                    border = BorderStroke(1.dp, ColorBorder)
                ) {
                    Column(modifier = Modifier.padding(16.dp)) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.SpaceBetween
                        ) {
                            Text(
                                text = "📊 COMPLETED TRADES",
                                style = MaterialTheme.typography.labelSmall.copy(fontSize = 10.sp),
                                color = ColorBlue,
                                fontWeight = FontWeight.Bold,
                                fontFamily = FontFamily.Monospace
                            )
                            Text(
                                text = "VIEW ALL (${tradingData.trades.size}) ↗",
                                style = MaterialTheme.typography.labelSmall,
                                color = ColorMuted,
                                fontFamily = FontFamily.Monospace,
                                fontSize = 9.sp
                            )
                        }
                        
                        Spacer(modifier = Modifier.height(12.dp))

                        if (tradingData.trades.isEmpty()) {
                            Box(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .height(80.dp),
                                contentAlignment = Alignment.Center
                            ) {
                                Text(
                                    text = "NO TRADES RECORDED YET",
                                    color = ColorMuted,
                                    style = MaterialTheme.typography.bodyMedium,
                                    fontFamily = FontFamily.Monospace
                                )
                            }
                        } else {
                            // Scrollable — shows ~2 trades height, scroll to see all
                            Column(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .height(182.dp)
                                    .verticalScroll(rememberScrollState())
                            ) {
                                tradingData.trades.forEach { trade ->
                                    TradeHistoryCard(trade = trade)
                                    Spacer(modifier = Modifier.height(8.dp))
                                }
                            }
                        }
                    }
                }

                Spacer(modifier = Modifier.height(16.dp))

                // 6. SYSTEM TERMINAL LOGS
                LiveLogConsoleCard(logs = tradingData.log)

                Spacer(modifier = Modifier.height(40.dp))
            }

            // Edit Limits Dialog Modal
            if (editingPosition != null) {
                AlertDialog(
                    onDismissRequest = { editingPosition = null },
                    containerColor = Color(0xFF090D1A),
                    modifier = Modifier.border(1.dp, ColorBlue.copy(alpha = 0.5f), RoundedCornerShape(28.dp)),
                    title = {
                        Text(
                            text = "💰 EDIT POSITION LIMITS",
                            color = ColorBlue,
                            fontFamily = FontFamily.Monospace,
                            fontWeight = FontWeight.Black,
                            fontSize = 16.sp
                        )
                    },
                    text = {
                        Column {
                            Text(
                                text = "ID: ${editingPosition?.tid}",
                                color = ColorMuted,
                                style = MaterialTheme.typography.bodySmall,
                                fontFamily = FontFamily.Monospace
                            )
                            Spacer(modifier = Modifier.height(16.dp))

                            OutlinedTextField(
                                value = editSl,
                                onValueChange = { editSl = it },
                                label = { Text("Stop Loss (SL)", color = ColorMuted) },
                                modifier = Modifier.fillMaxWidth(),
                                textStyle = LocalTextStyle.current.copy(color = ColorText, fontFamily = FontFamily.Monospace),
                                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number)
                            )
                            Spacer(modifier = Modifier.height(12.dp))

                            OutlinedTextField(
                                value = editTp,
                                onValueChange = { editTp = it },
                                label = { Text("Take Profit (TP)", color = ColorMuted) },
                                modifier = Modifier.fillMaxWidth(),
                                textStyle = LocalTextStyle.current.copy(color = ColorText, fontFamily = FontFamily.Monospace),
                                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number)
                            )
                            Spacer(modifier = Modifier.height(16.dp))

                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.SpaceBetween
                            ) {
                                Text(
                                    text = "Trailing Stop Loss",
                                    color = ColorText,
                                    fontFamily = FontFamily.Monospace,
                                    style = MaterialTheme.typography.bodyMedium
                                )
                                Switch(
                                    checked = editTsl,
                                    onCheckedChange = { editTsl = it },
                                    colors = SwitchDefaults.colors(
                                        checkedThumbColor = ColorGreen,
                                        checkedTrackColor = ColorGreen.copy(alpha = 0.3f)
                                    )
                                )
                            }
                        }
                    },
                    confirmButton = {
                        Button(
                            onClick = {
                                val pos = editingPosition ?: return@Button
                                isUpdatingPosition = true
                                CoroutineScope(Dispatchers.Main).launch {
                                    val success = updatePositionLimits(
                                        baseUrl = baseUrl,
                                        tid = pos.tid,
                                        sl = editSl.toDoubleOrNull() ?: pos.sl,
                                        tp = editTp.toDoubleOrNull() ?: pos.tp,
                                        tsl = editTsl
                                    )
                                    isUpdatingPosition = false
                                    if (success) {
                                        try {
                                            tradingData = fetchDashboardData(baseUrl)
                                        } catch (e: Exception) {}
                                        editingPosition = null
                                    }
                                }
                            },
                            modifier = Modifier.clip(RoundedCornerShape(8.dp)),
                            colors = ButtonDefaults.buttonColors(containerColor = ColorGreen),
                            enabled = !isUpdatingPosition
                        ) {
                            Text("UPDATE LIMITS", color = ColorBG, fontWeight = FontWeight.Black, fontFamily = FontFamily.Monospace)
                        }
                    },
                    dismissButton = {
                        TextButton(onClick = { editingPosition = null }) {
                            Text("CANCEL", color = ColorRed, fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold)
                        }
                    }
                )
            }

            // Live Broker Authentication Dialog Modal
            if (showAuthDialog) {
                AlertDialog(
                    onDismissRequest = { 
                        if (!authIsSubmitting) showAuthDialog = false 
                    },
                    containerColor = Color(0xFF090D1A),
                    modifier = Modifier.border(1.5.dp, ColorRed.copy(alpha = 0.5f), RoundedCornerShape(24.dp)),
                    title = {
                        Text(
                            text = "🔐 ${authBroker.uppercase()} LIVE LOGIN",
                            color = ColorRed,
                            style = MaterialTheme.typography.titleMedium,
                            fontFamily = FontFamily.Monospace,
                            fontWeight = FontWeight.Bold
                        )
                    },
                    text = {
                        Column(modifier = Modifier.fillMaxWidth()) {
                            Text(
                                text = "Live trading requires secure broker authentication. Enter your live credentials below:",
                                color = ColorText,
                                fontSize = 11.sp,
                                fontFamily = FontFamily.Monospace,
                                modifier = Modifier.padding(bottom = 12.dp)
                            )
                            
                            OutlinedTextField(
                                value = authClientId,
                                onValueChange = { authClientId = it },
                                label = { Text("Client ID", color = ColorMuted, fontFamily = FontFamily.Monospace) },
                                singleLine = true,
                                modifier = Modifier.fillMaxWidth(),
                                textStyle = LocalTextStyle.current.copy(color = ColorText, fontFamily = FontFamily.Monospace),
                                colors = OutlinedTextFieldDefaults.colors(
                                    focusedBorderColor = ColorBlue,
                                    unfocusedBorderColor = ColorBorder
                                )
                            )
                            
                            Spacer(modifier = Modifier.height(12.dp))
                            
                            val hasToken = if (authBroker == "DHAN") tradingData.dhanHasToken else tradingData.growwHasToken
                            if (hasToken) {
                                Row(
                                    verticalAlignment = Alignment.CenterVertically,
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .clickable { authReuseSavedToken = !authReuseSavedToken }
                                        .padding(vertical = 4.dp)
                                ) {
                                    Checkbox(
                                        checked = authReuseSavedToken,
                                        onCheckedChange = { authReuseSavedToken = it },
                                        colors = CheckboxDefaults.colors(checkedColor = ColorGreen)
                                    )
                                    Spacer(modifier = Modifier.width(6.dp))
                                    Text(
                                        text = "Reuse saved secure token",
                                        color = ColorGreen,
                                        fontSize = 11.sp,
                                        fontFamily = FontFamily.Monospace
                                    )
                                }
                            }
                            
                            if (!authReuseSavedToken) {
                                OutlinedTextField(
                                    value = authAccessToken,
                                    onValueChange = { authAccessToken = it },
                                    label = { 
                                        Text(
                                            text = if (authBroker == "DHAN") "Access Token" else "Groww PIN", 
                                            color = ColorMuted, 
                                            fontFamily = FontFamily.Monospace
                                        ) 
                                    },
                                    singleLine = true,
                                    modifier = Modifier.fillMaxWidth(),
                                    textStyle = LocalTextStyle.current.copy(color = ColorText, fontFamily = FontFamily.Monospace),
                                    colors = OutlinedTextFieldDefaults.colors(
                                        focusedBorderColor = ColorBlue,
                                        unfocusedBorderColor = ColorBorder
                                    )
                                )
                            }
                            
                            authErrorMessage?.let { err ->
                                Spacer(modifier = Modifier.height(12.dp))
                                Text(
                                    text = "❌ $err",
                                    color = ColorRed,
                                    fontSize = 11.sp,
                                    fontWeight = FontWeight.Bold,
                                    fontFamily = FontFamily.Monospace
                                )
                            }
                        }
                    },
                    confirmButton = {
                        Button(
                            onClick = {
                                authErrorMessage = null
                                if (authClientId.trim().isEmpty()) {
                                    authErrorMessage = "Client ID is required."
                                    return@Button
                                }
                                val token = if (authReuseSavedToken) "REUSE_SAVED_TOKEN" else authAccessToken.trim()
                                if (token.isEmpty()) {
                                    authErrorMessage = if (authBroker == "DHAN") "Access Token is required." else "PIN is required."
                                    return@Button
                                }
                                
                                authIsSubmitting = true
                                coroutineScope.launch {
                                    val (success, message) = submitBrokerCredentials(baseUrl, authBroker, authClientId.trim(), token)
                                    if (success) {
                                        onAuthSuccessAction?.invoke()
                                        showAuthDialog = false
                                    } else {
                                        authErrorMessage = message
                                    }
                                    authIsSubmitting = false
                                }
                            },
                            colors = ButtonDefaults.buttonColors(containerColor = ColorGreen.copy(alpha = 0.2f)),
                            border = BorderStroke(1.dp, ColorGreen),
                            modifier = Modifier.height(36.dp),
                            shape = RoundedCornerShape(6.dp),
                            enabled = !authIsSubmitting
                        ) {
                            Text(
                                text = if (authIsSubmitting) "AUTH..." else "CONFIRM LOGIN", 
                                color = ColorGreen, 
                                fontFamily = FontFamily.Monospace,
                                fontSize = 10.sp,
                                fontWeight = FontWeight.Black
                            )
                        }
                    },
                    dismissButton = {
                        TextButton(
                            onClick = { showAuthDialog = false },
                            enabled = !authIsSubmitting
                        ) {
                            Text(
                                text = "CANCEL", 
                                color = ColorMuted, 
                                fontFamily = FontFamily.Monospace,
                                fontSize = 10.sp
                            )
                        }
                    }
                )
            }

            // 6. ENLARGED FULLSCREEN TRADE HISTORY OVERLAY WITH BLUR
            AnimatedVisibility(
                visible = showFullHistory,
                enter = fadeIn() + slideInVertically(initialOffsetY = { it / 2 }),
                exit = fadeOut() + slideOutVertically(targetOffsetY = { it / 2 })
            ) {
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .background(ColorBG.copy(alpha = 0.85f))
                        .clickable { showFullHistory = false }
                        .padding(20.dp),
                    contentAlignment = Alignment.Center
                ) {
                    Card(
                        modifier = Modifier
                            .fillMaxWidth()
                            .fillMaxHeight(0.85f)
                            .clickable(enabled = false) {}
                            .border(1.5.dp, ColorBlue, RoundedCornerShape(24.dp)),
                        shape = RoundedCornerShape(24.dp),
                        colors = CardDefaults.cardColors(containerColor = Color(0xFF090D1A))
                    ) {
                        Column(
                            modifier = Modifier
                                .fillMaxSize()
                                .padding(20.dp)
                        ) {
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.SpaceBetween
                            ) {
                                Text(
                                    text = "📊 COMPLETED TRADES",
                                    color = ColorBlue,
                                    style = MaterialTheme.typography.titleMedium,
                                    fontWeight = FontWeight.Black,
                                    fontFamily = FontFamily.Monospace
                                )
                                IconButton(
                                    onClick = { showFullHistory = false },
                                    modifier = Modifier
                                        .size(36.dp)
                                        .clip(RoundedCornerShape(18.dp))
                                        .background(ColorRed.copy(alpha = 0.15f))
                                        .border(1.dp, ColorRed, RoundedCornerShape(18.dp))
                                ) {
                                    Text(
                                        text = "✕",
                                        color = ColorRed,
                                        fontSize = 14.sp,
                                        fontWeight = FontWeight.Bold
                                    )
                                }
                            }
                            
                            Spacer(modifier = Modifier.height(16.dp))

                            LazyColumn(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .weight(1f)
                            ) {
                                items(tradingData.trades) { trade ->
                                    TradeHistoryCard(trade = trade)
                                    Spacer(modifier = Modifier.height(8.dp))
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
}

// =============================================================================
// SUB-COMPOSABLES
// =============================================================================

@Composable
fun LiveIndexHeader(
    nifty: Double,
    sensex: Double,
    vix: Double,
    live: Boolean,
    broker: String
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = ColorCardBG),
        border = BorderStroke(1.dp, ColorBorder)
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text(
                    text = "🔌 REAL-TIME INDEX TRACKER",
                    style = MaterialTheme.typography.labelSmall.copy(fontSize = 10.sp),
                    color = ColorBlue,
                    fontWeight = FontWeight.Bold,
                    fontFamily = FontFamily.Monospace
                )
                Box(
                    modifier = Modifier
                        .clip(RoundedCornerShape(6.dp))
                        .background(if (live) ColorGreen.copy(alpha = 0.15f) else ColorBlue.copy(alpha = 0.15f))
                        .border(1.dp, if (live) ColorGreen else ColorBlue, RoundedCornerShape(6.dp))
                        .padding(horizontal = 8.dp, vertical = 2.dp)
                ) {
                    Text(
                        text = if (live) "LIVE | $broker" else "PAPER TRADING",
                        style = MaterialTheme.typography.bodySmall.copy(fontSize = 9.sp),
                        color = if (live) ColorGreen else ColorBlue,
                        fontWeight = FontWeight.Black,
                        fontFamily = FontFamily.Monospace
                    )
                }
            }

            Spacer(modifier = Modifier.height(16.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Column {
                    Text(text = "NIFTY SPOT", style = MaterialTheme.typography.labelSmall, color = ColorMuted, fontFamily = FontFamily.Monospace)
                    Text(
                        text = if (nifty > 0) String.format("%,.2f", nifty) else "—",
                        style = MaterialTheme.typography.titleMedium,
                        color = ColorText,
                        fontWeight = FontWeight.Bold,
                        fontFamily = FontFamily.Monospace
                    )
                }
                Column {
                    Text(text = "SENSEX SPOT", style = MaterialTheme.typography.labelSmall, color = ColorMuted, fontFamily = FontFamily.Monospace)
                    Text(
                        text = if (sensex > 0) String.format("%,.2f", sensex) else "—",
                        style = MaterialTheme.typography.titleMedium,
                        color = ColorText,
                        fontWeight = FontWeight.Bold,
                        fontFamily = FontFamily.Monospace
                    )
                }
                Column {
                    Text(text = "INDIA VIX", style = MaterialTheme.typography.labelSmall, color = ColorMuted, fontFamily = FontFamily.Monospace)
                    Text(
                        text = String.format("%.2f", vix),
                        style = MaterialTheme.typography.titleMedium,
                        color = if (vix > 20.0) ColorRed else ColorGreen,
                        fontWeight = FontWeight.Bold,
                        fontFamily = FontFamily.Monospace
                    )
                }
            }
        }
    }
}

@Composable
fun DayPnlCard(
    pnl: Double,
    capital: Double,
    baseCapital: Double,
    totalTrades: Int
) {
    val isProfit = pnl >= 0
    val glowColor = if (isProfit) ColorGreen else ColorRed
    val cardBorder = if (isProfit) ColorGreen.copy(alpha = 0.25f) else ColorRed.copy(alpha = 0.25f)
    val cardBackground = if (isProfit) ColorGreen.copy(alpha = 0.05f) else ColorRed.copy(alpha = 0.05f)

    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = ColorCardBG),
        border = BorderStroke(1.dp, cardBorder)
    ) {
        // Linear colored accent bar at top of the card
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(4.dp)
                .background(glowColor)
        )

        Column(
            modifier = Modifier
                .background(cardBackground)
                .padding(20.dp),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Text(
                text = "💰 NET REAL-TIME DAY P&L",
                style = MaterialTheme.typography.labelMedium,
                color = ColorMuted,
                fontFamily = FontFamily.Monospace,
                fontWeight = FontWeight.Bold
            )
            Spacer(modifier = Modifier.height(8.dp))
            Text(
                text = String.format("Rs. %,+.2f", pnl),
                style = MaterialTheme.typography.headlineMedium.copy(fontSize = 26.sp),
                color = glowColor,
                fontWeight = FontWeight.Black,
                fontFamily = FontFamily.Monospace
            )
            Spacer(modifier = Modifier.height(16.dp))

            // Spacer line
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(1.dp)
                    .background(ColorBorder)
            )
            Spacer(modifier = Modifier.height(12.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Column(horizontalAlignment = Alignment.Start) {
                    Text(text = "CURRENT CAPITAL", style = MaterialTheme.typography.labelSmall, color = ColorMuted, fontFamily = FontFamily.Monospace)
                    Text(
                        text = String.format("Rs. %,.0f", capital),
                        style = MaterialTheme.typography.bodyMedium,
                        color = ColorText,
                        fontWeight = FontWeight.Bold,
                        fontFamily = FontFamily.Monospace
                    )
                }
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(text = "BASE CAPITAL", style = MaterialTheme.typography.labelSmall, color = ColorMuted, fontFamily = FontFamily.Monospace)
                    Text(
                        text = String.format("Rs. %,.0f", baseCapital),
                        style = MaterialTheme.typography.bodyMedium,
                        color = ColorText,
                        fontWeight = FontWeight.Bold,
                        fontFamily = FontFamily.Monospace
                    )
                }
                Column(horizontalAlignment = Alignment.End) {
                    Text(text = "TOTAL TRADES", style = MaterialTheme.typography.labelSmall, color = ColorMuted, fontFamily = FontFamily.Monospace)
                    Text(
                        text = "$totalTrades",
                        style = MaterialTheme.typography.bodyMedium,
                        color = ColorText,
                        fontWeight = FontWeight.Bold,
                        fontFamily = FontFamily.Monospace
                    )
                }
            }
        }
    }
}

@Composable
fun DrawEquityCurve(equity: List<Double>, trades: List<Trade>, baseCapital: Double) {
    if (equity.isEmpty()) {
        Text(
            text = "No trades recorded in current session",
            style = MaterialTheme.typography.bodySmall,
            color = ColorMuted,
            fontFamily = FontFamily.Monospace
        )
        return
    }

    val data = remember(equity) { listOf(0.0) + equity }
    var selectedIndex by remember { mutableStateOf<Int?>(null) }

    Box(modifier = Modifier.fillMaxSize()) {
        Canvas(
            modifier = Modifier
                .fillMaxSize()
                .pointerInput(data) {
                    awaitPointerEventScope {
                        while (true) {
                            val event = awaitPointerEvent()
                            val change = event.changes.firstOrNull()
                            if (change != null) {
                                if (change.pressed) {
                                    val x = change.position.x
                                    val pct = (x / size.width.toFloat()).coerceIn(0f, 1f)
                                    val selected = Math.round(pct * (data.size - 1)).toInt()
                                    selectedIndex = selected
                                    change.consume()
                                } else {
                                    selectedIndex = null
                                }
                            } else {
                                selectedIndex = null
                            }
                        }
                    }
                }
        ) {
            val width = size.width
            val height = size.height

            val maxVal = data.maxOrNull() ?: 1000.0
            val minVal = data.minOrNull() ?: -1000.0
            val range = if (maxVal - minVal == 0.0) 2000.0 else maxVal - minVal
            
            val points = data.mapIndexed { index, value ->
                val x = if (data.size > 1) index.toFloat() / (data.size - 1) * width else 0f
                val y = height - ((value - minVal) / range * height).toFloat()
                androidx.compose.ui.geometry.Offset(x, y)
            }

            val gridLinesCount = 3
            for (i in 0..gridLinesCount) {
                val ratio = i.toFloat() / gridLinesCount
                val yGrid = height * ratio
                drawLine(
                    color = ColorBorder.copy(alpha = 0.2f),
                    start = androidx.compose.ui.geometry.Offset(0f, yGrid),
                    end = androidx.compose.ui.geometry.Offset(width, yGrid),
                    strokeWidth = 1.dp.toPx()
                )
            }

            val zeroY = height - ((0.0 - minVal) / range * height).toFloat()

            val fillPath = Path().apply {
                if (points.isNotEmpty()) {
                    moveTo(points[0].x, zeroY)
                    lineTo(points[0].x, points[0].y)
                    for (i in 1 until points.size) {
                        val pPrev = points[i - 1]
                        val pCurr = points[i]
                        val controlX = (pPrev.x + pCurr.x) / 2f
                        cubicTo(controlX, pPrev.y, controlX, pCurr.y, pCurr.x, pCurr.y)
                    }
                    lineTo(points.last().x, zeroY)
                    close()
                }
            }

            // 1. Draw Positive Gradient (above zeroY)
            clipRect(top = 0f, bottom = zeroY) {
                drawPath(
                    path = fillPath,
                    brush = Brush.verticalGradient(
                        colors = listOf(
                            ColorGreen.copy(alpha = 0.3f),
                            ColorBG.copy(alpha = 0f)
                        ),
                        startY = 0f,
                        endY = zeroY
                    )
                )
            }

            // 2. Draw Negative Gradient (below zeroY)
            clipRect(top = zeroY, bottom = height) {
                drawPath(
                    path = fillPath,
                    brush = Brush.verticalGradient(
                        colors = listOf(
                            ColorBG.copy(alpha = 0f),
                            ColorRed.copy(alpha = 0.3f)
                        ),
                        startY = zeroY,
                        endY = height
                    )
                )
            }

            // 3. Highlight Horizontal Zero Baseline in Red
            drawLine(
                color = ColorRed.copy(alpha = 0.45f),
                start = androidx.compose.ui.geometry.Offset(0f, zeroY),
                end = androidx.compose.ui.geometry.Offset(width, zeroY),
                strokeWidth = 2.dp.toPx()
            )

            // 4. Draw Conditional Line Segments (Green above zeroY, Red below zeroY)
            for (i in 1 until points.size) {
                val pPrev = points[i - 1]
                val pCurr = points[i]

                if (pPrev.y <= zeroY && pCurr.y <= zeroY) {
                    // Entirely positive
                    val segPath = Path().apply {
                        moveTo(pPrev.x, pPrev.y)
                        val controlX = (pPrev.x + pCurr.x) / 2f
                        cubicTo(controlX, pPrev.y, controlX, pCurr.y, pCurr.x, pCurr.y)
                    }
                    drawPath(
                        path = segPath,
                        color = ColorGreen,
                        style = Stroke(width = 2.5.dp.toPx(), cap = StrokeCap.Round)
                    )
                } else if (pPrev.y > zeroY && pCurr.y > zeroY) {
                    // Entirely negative
                    val segPath = Path().apply {
                        moveTo(pPrev.x, pPrev.y)
                        val controlX = (pPrev.x + pCurr.x) / 2f
                        cubicTo(controlX, pPrev.y, controlX, pCurr.y, pCurr.x, pCurr.y)
                    }
                    drawPath(
                        path = segPath,
                        color = ColorRed,
                        style = Stroke(width = 2.5.dp.toPx(), cap = StrokeCap.Round)
                    )
                } else {
                    // Crosses zero line: interpolate intersection
                    val t = (zeroY - pPrev.y) / (pCurr.y - pPrev.y)
                    val intersectX = pPrev.x + t * (pCurr.x - pPrev.x)
                    val intersectPoint = androidx.compose.ui.geometry.Offset(intersectX, zeroY)

                    val seg1 = Path().apply {
                        moveTo(pPrev.x, pPrev.y)
                        val controlX = (pPrev.x + intersectPoint.x) / 2f
                        cubicTo(controlX, pPrev.y, controlX, zeroY, intersectPoint.x, zeroY)
                    }
                    val seg2 = Path().apply {
                        moveTo(intersectPoint.x, zeroY)
                        val controlX = (intersectPoint.x + pCurr.x) / 2f
                        cubicTo(controlX, zeroY, controlX, pCurr.y, pCurr.x, pCurr.y)
                    }

                    if (pPrev.y <= zeroY) {
                        drawPath(path = seg1, color = ColorGreen, style = Stroke(width = 2.5.dp.toPx(), cap = StrokeCap.Round))
                        drawPath(path = seg2, color = ColorRed, style = Stroke(width = 2.5.dp.toPx(), cap = StrokeCap.Round))
                    } else {
                        drawPath(path = seg1, color = ColorRed, style = Stroke(width = 2.5.dp.toPx(), cap = StrokeCap.Round))
                        drawPath(path = seg2, color = ColorGreen, style = Stroke(width = 2.5.dp.toPx(), cap = StrokeCap.Round))
                    }
                }
            }

            // Draw selection guide and dot if sliding
            val currentSel = selectedIndex
            if (currentSel != null && currentSel in points.indices) {
                val selPoint = points[currentSel]
                
                // Vertical slider line
                drawLine(
                    color = ColorBlue.copy(alpha = 0.6f),
                    start = androidx.compose.ui.geometry.Offset(selPoint.x, 0f),
                    end = androidx.compose.ui.geometry.Offset(selPoint.x, height),
                    strokeWidth = 1.5.dp.toPx()
                )

                // Selected point glow circle
                drawCircle(
                    color = ColorBlue.copy(alpha = 0.4f),
                    radius = 9.dp.toPx(),
                    center = selPoint
                )
                drawCircle(
                    color = Color(0xFFFFFFFF),
                    radius = 4.dp.toPx(),
                    center = selPoint
                )
            } else if (points.isNotEmpty()) {
                val lastPoint = points.last()
                drawCircle(
                    color = ColorGreen.copy(alpha = 0.4f),
                    radius = 7.dp.toPx(),
                    center = lastPoint
                )
                drawCircle(
                    color = Color(0xFFFFFFFF),
                    radius = 3.dp.toPx(),
                    center = lastPoint
                )
            }
        }

        // Overlay glassmorphic card for details when sliding
        val currentSel = selectedIndex
        if (currentSel != null && currentSel in data.indices) {
            val pnl = data[currentSel]
            val balance = baseCapital + pnl

            Card(
                modifier = Modifier
                    .align(Alignment.TopCenter)
                    .padding(top = 4.dp),
                shape = RoundedCornerShape(8.dp),
                colors = CardDefaults.cardColors(containerColor = Color(0xDD0D111A)),
                border = BorderStroke(1.dp, ColorBlue.copy(alpha = 0.5f))
            ) {
                Column(
                    modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
                    horizontalAlignment = Alignment.CenterHorizontally
                ) {
                    if (currentSel == 0) {
                        Text(
                            text = "🏁 START OF SESSION",
                            color = ColorBlue,
                            fontSize = 9.sp,
                            fontWeight = FontWeight.Black,
                            fontFamily = FontFamily.Monospace
                        )
                        Text(
                            text = String.format("Capital: Rs. %,.0f", baseCapital),
                            color = ColorText,
                            fontSize = 10.sp,
                            fontWeight = FontWeight.Bold,
                            fontFamily = FontFamily.Monospace
                        )
                    } else {
                        // Chronological index in trades list: currentSel - 1
                        val tradeIdx = currentSel - 1
                        val trade = if (tradeIdx in trades.indices) trades[tradeIdx] else null
                        if (trade != null) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Box(
                                    modifier = Modifier
                                        .clip(RoundedCornerShape(3.dp))
                                        .background(if (trade.direction == "BUY") ColorBlue.copy(alpha = 0.15f) else ColorGold.copy(alpha = 0.15f))
                                        .border(0.5.dp, if (trade.direction == "BUY") ColorBlue else ColorGold, RoundedCornerShape(3.dp))
                                        .padding(horizontal = 4.dp, vertical = 1.dp)
                                ) {
                                    Text(
                                        text = trade.direction,
                                        fontSize = 8.sp,
                                        fontWeight = FontWeight.Black,
                                        fontFamily = FontFamily.Monospace,
                                        color = if (trade.direction == "BUY") ColorBlue else ColorGold
                                    )
                                }
                                Spacer(modifier = Modifier.width(6.dp))
                                Text(
                                    text = "${trade.strike}${trade.opt} @ ${trade.exitTime}",
                                    color = ColorMuted,
                                    fontSize = 8.sp,
                                    fontFamily = FontFamily.Monospace
                                )
                            }
                            Spacer(modifier = Modifier.height(2.dp))
                            Row {
                                Text(
                                    text = String.format("Trade P&L: Rs.%,.0f", trade.pnl),
                                    color = if (trade.pnl >= 0) ColorGreen else ColorRed,
                                    fontSize = 9.sp,
                                    fontWeight = FontWeight.Bold,
                                    fontFamily = FontFamily.Monospace
                                )
                                Spacer(modifier = Modifier.width(8.dp))
                                Text(
                                    text = String.format("Balance: Rs.%,.0f", balance),
                                    color = ColorText,
                                    fontSize = 9.sp,
                                    fontWeight = FontWeight.Bold,
                                    fontFamily = FontFamily.Monospace
                                )
                            }
                        } else {
                            Text(
                                text = String.format("Trade #%d | P&L: Rs.%,.0f", currentSel, pnl),
                                color = if (pnl >= 0) ColorGreen else ColorRed,
                                fontSize = 9.sp,
                                fontWeight = FontWeight.Bold,
                                fontFamily = FontFamily.Monospace
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun DrawWinLossChart(trades: List<Trade>) {
    if (trades.isEmpty()) {
        Text(
            text = "No trading transactions recorded yet",
            style = MaterialTheme.typography.bodySmall,
            color = ColorMuted,
            fontFamily = FontFamily.Monospace
        )
        return
    }

    var selectedIndex by remember { mutableStateOf<Int?>(null) }

    Box(modifier = Modifier.fillMaxSize()) {
        Canvas(
            modifier = Modifier
                .fillMaxSize()
                .pointerInput(trades) {
                    awaitPointerEventScope {
                        while (true) {
                            val event = awaitPointerEvent()
                            val change = event.changes.firstOrNull()
                            if (change != null) {
                                if (change.pressed) {
                                    val x = change.position.x
                                    val pct = (x / size.width.toFloat()).coerceIn(0f, 1f)
                                    val selected = Math.round(pct * (trades.size - 1)).toInt()
                                    selectedIndex = selected
                                    change.consume()
                                } else {
                                    selectedIndex = null
                                }
                            } else {
                                selectedIndex = null
                            }
                        }
                    }
                }
        ) {
            val width = size.width
            val height = size.height

            val maxPnl = trades.maxOfOrNull { Math.abs(it.pnl) } ?: 1000.0
            val range = if (maxPnl == 0.0) 1000.0 else maxPnl

            val barSpacing = 6.dp.toPx()
            val totalSpacing = barSpacing * (trades.size + 1)
            val availableWidth = width - totalSpacing
            val barWidth = if (trades.size > 0) availableWidth / trades.size else 30.dp.toPx()

            val zeroY = height / 2f

            drawLine(
                color = ColorBorder.copy(alpha = 0.5f),
                start = androidx.compose.ui.geometry.Offset(0f, zeroY),
                end = androidx.compose.ui.geometry.Offset(width, zeroY),
                strokeWidth = 1.5.dp.toPx()
            )

            trades.forEachIndexed { index, trade ->
                val x = barSpacing + index * (barWidth + barSpacing)
                val pnl = trade.pnl
                
                val scaledVal = (Math.abs(pnl) / range * (height / 2f)).toFloat()
                val y = if (pnl >= 0) zeroY - scaledVal else zeroY
                
                val barColor = if (pnl >= 0) ColorGreen else ColorRed
                
                drawRoundRect(
                    color = barColor.copy(alpha = if (selectedIndex == index) 1f else 0.85f),
                    topLeft = androidx.compose.ui.geometry.Offset(x, y),
                    size = androidx.compose.ui.geometry.Size(barWidth, scaledVal.coerceAtLeast(2.dp.toPx())),
                    cornerRadius = androidx.compose.ui.geometry.CornerRadius(3.dp.toPx(), 3.dp.toPx())
                )

                drawRoundRect(
                    color = if (selectedIndex == index) ColorBlue else barColor,
                    topLeft = androidx.compose.ui.geometry.Offset(x, y),
                    size = androidx.compose.ui.geometry.Size(barWidth, scaledVal.coerceAtLeast(2.dp.toPx())),
                    cornerRadius = androidx.compose.ui.geometry.CornerRadius(3.dp.toPx(), 3.dp.toPx()),
                    style = Stroke(width = if (selectedIndex == index) 2.dp.toPx() else 1.dp.toPx())
                )
            }

            // Draw selection guide line if sliding
            val currentSel = selectedIndex
            if (currentSel != null && currentSel in trades.indices) {
                val x = barSpacing + currentSel * (barWidth + barSpacing) + (barWidth / 2f)
                drawLine(
                    color = ColorBlue.copy(alpha = 0.6f),
                    start = androidx.compose.ui.geometry.Offset(x, 0f),
                    end = androidx.compose.ui.geometry.Offset(x, height),
                    strokeWidth = 1.5.dp.toPx()
                )
            }
        }

        // Overlay glassmorphic card for trade details when sliding
        val currentSel = selectedIndex
        if (currentSel != null && currentSel in trades.indices) {
            val trade = trades[currentSel]
            val isWin = trade.pnl >= 0
            val pnlColor = if (isWin) ColorGreen else ColorRed

            Card(
                modifier = Modifier
                    .align(Alignment.TopCenter)
                    .padding(top = 4.dp),
                shape = RoundedCornerShape(8.dp),
                colors = CardDefaults.cardColors(containerColor = Color(0xDD0D111A)),
                border = BorderStroke(1.dp, ColorBlue.copy(alpha = 0.5f))
            ) {
                Column(
                    modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
                    horizontalAlignment = Alignment.CenterHorizontally
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Box(
                            modifier = Modifier
                                .clip(RoundedCornerShape(3.dp))
                                .background(if (trade.direction == "BUY") ColorBlue.copy(alpha = 0.15f) else ColorGold.copy(alpha = 0.15f))
                                .border(0.5.dp, if (trade.direction == "BUY") ColorBlue else ColorGold, RoundedCornerShape(3.dp))
                                .padding(horizontal = 4.dp, vertical = 1.dp)
                        ) {
                            Text(
                                text = trade.direction,
                                fontSize = 8.sp,
                                fontWeight = FontWeight.Black,
                                fontFamily = FontFamily.Monospace,
                                color = if (trade.direction == "BUY") ColorBlue else ColorGold
                            )
                        }
                        Spacer(modifier = Modifier.width(6.dp))
                        Text(
                            text = "${trade.strike}${trade.opt} @ ${trade.exitTime}",
                            color = ColorMuted,
                            fontSize = 8.sp,
                            fontFamily = FontFamily.Monospace
                        )
                    }
                    Spacer(modifier = Modifier.height(2.dp))
                    Row {
                        Text(
                            text = String.format("Trade P&L: Rs.%,.2f", trade.pnl),
                            color = pnlColor,
                            fontSize = 9.sp,
                            fontWeight = FontWeight.Bold,
                            fontFamily = FontFamily.Monospace
                        )
                        Spacer(modifier = Modifier.width(8.dp))
                        Text(
                            text = "Exit: ${trade.reason.replace('_', ' ')}",
                            color = ColorText,
                            fontSize = 9.sp,
                            fontWeight = FontWeight.Bold,
                            fontFamily = FontFamily.Monospace
                        )
                    }
                }
            }
        }
    }
}

@Composable
fun DrawDrawdownChart(equity: List<Double>, baseCapital: Double, trades: List<Trade>) {
    if (equity.isEmpty()) {
        Text(
            text = "No closed trades data yet",
            style = MaterialTheme.typography.bodySmall,
            color = ColorMuted,
            fontFamily = FontFamily.Monospace
        )
        return
    }

    val base = if (baseCapital > 0) baseCapital else 100000.0
    val capitalSeries = remember(equity, base) {
        val series = mutableListOf(base)
        var runningCapital = base
        for (p in equity) {
            runningCapital = base + p
            series.add(runningCapital)
        }
        series
    }

    val drawdownSeries = remember(capitalSeries) {
        val drawdowns = mutableListOf<Double>()
        var peak = capitalSeries.first()
        for (cap in capitalSeries) {
            if (cap > peak) {
                peak = cap
            }
            val dd = if (peak > 0) -(peak - cap) else 0.0
            drawdowns.add(dd)
        }
        drawdowns
    }

    var selectedIndex by remember { mutableStateOf<Int?>(null) }

    Box(modifier = Modifier.fillMaxSize()) {
        Canvas(
            modifier = Modifier
                .fillMaxSize()
                .pointerInput(drawdownSeries) {
                    awaitPointerEventScope {
                        while (true) {
                            val event = awaitPointerEvent()
                            val change = event.changes.firstOrNull()
                            if (change != null) {
                                if (change.pressed) {
                                    val x = change.position.x
                                    val pct = (x / size.width.toFloat()).coerceIn(0f, 1f)
                                    val selected = Math.round(pct * (drawdownSeries.size - 1)).toInt()
                                    selectedIndex = selected
                                    change.consume()
                                } else {
                                    selectedIndex = null
                                }
                            } else {
                                selectedIndex = null
                            }
                        }
                    }
                }
        ) {
            val width = size.width
            val height = size.height

            val maxDD = drawdownSeries.minOrNull() ?: -1000.0
            val minVal = maxDD
            val maxVal = 0.0
            val range = if (maxVal - minVal == 0.0) 1000.0 else maxVal - minVal

            val points = drawdownSeries.mapIndexed { index, value ->
                val x = if (drawdownSeries.size > 1) index.toFloat() / (drawdownSeries.size - 1) * width else 0f
                val y = height - ((value - minVal) / range * height).toFloat()
                androidx.compose.ui.geometry.Offset(x, y)
            }

            val gridLinesCount = 3
            for (i in 0..gridLinesCount) {
                val ratio = i.toFloat() / gridLinesCount
                val yGrid = height * ratio
                drawLine(
                    color = ColorBorder.copy(alpha = 0.15f),
                    start = androidx.compose.ui.geometry.Offset(0f, yGrid),
                    end = androidx.compose.ui.geometry.Offset(width, yGrid),
                    strokeWidth = 1.dp.toPx()
                )
            }

            val path = Path().apply {
                if (points.isNotEmpty()) {
                    moveTo(points[0].x, points[0].y)
                    for (i in 1 until points.size) {
                        val pPrev = points[i - 1]
                        val pCurr = points[i]
                        val controlX = (pPrev.x + pCurr.x) / 2f
                        cubicTo(controlX, pPrev.y, controlX, pCurr.y, pCurr.x, pCurr.y)
                    }
                }
            }

            val fillPath = Path().apply {
                if (points.isNotEmpty()) {
                    moveTo(points[0].x, 0f)
                    lineTo(points[0].x, points[0].y)
                    for (i in 1 until points.size) {
                        val pPrev = points[i - 1]
                        val pCurr = points[i]
                        val controlX = (pPrev.x + pCurr.x) / 2f
                        cubicTo(controlX, pPrev.y, controlX, pCurr.y, pCurr.x, pCurr.y)
                    }
                    lineTo(width, 0f)
                    close()
                }
            }

            drawPath(
                path = fillPath,
                brush = Brush.verticalGradient(
                    colors = listOf(
                        ColorRed.copy(alpha = 0f),
                        ColorRed.copy(alpha = 0.35f)
                    ),
                    startY = 0f,
                    endY = height
                )
            )

            drawPath(
                path = path,
                color = ColorRed,
                style = Stroke(width = 2.dp.toPx(), cap = StrokeCap.Round)
            )

            // Highlight Zero Baseline (drawdown peak of 0 is at y = 0f)
            drawLine(
                color = ColorRed.copy(alpha = 0.5f),
                start = androidx.compose.ui.geometry.Offset(0f, 0f),
                end = androidx.compose.ui.geometry.Offset(width, 0f),
                strokeWidth = 2.dp.toPx()
            )

            // Draw selection guide and dot if sliding
            val currentSel = selectedIndex
            if (currentSel != null && currentSel in points.indices) {
                val selPoint = points[currentSel]
                
                // Vertical slider line
                drawLine(
                    color = ColorBlue.copy(alpha = 0.6f),
                    start = androidx.compose.ui.geometry.Offset(selPoint.x, 0f),
                    end = androidx.compose.ui.geometry.Offset(selPoint.x, height),
                    strokeWidth = 1.5.dp.toPx()
                )

                // Selected point glow circle
                drawCircle(
                    color = ColorBlue.copy(alpha = 0.4f),
                    radius = 8.dp.toPx(),
                    center = selPoint
                )
                drawCircle(
                    color = Color(0xFFFFFFFF),
                    radius = 3.5.dp.toPx(),
                    center = selPoint
                )
            } else if (points.isNotEmpty()) {
                val lastPoint = points.last()
                drawCircle(
                    color = ColorRed.copy(alpha = 0.4f),
                    radius = 6.dp.toPx(),
                    center = lastPoint
                )
                drawCircle(
                    color = Color(0xFFFFFFFF),
                    radius = 2.5.dp.toPx(),
                    center = lastPoint
                )
            }
        }

        // Overlay glassmorphic card for drawdown details when sliding
        val currentSel = selectedIndex
        if (currentSel != null && currentSel in drawdownSeries.indices) {
            val ddAmount = drawdownSeries[currentSel]
            val cap = capitalSeries[currentSel]
            val ddPct = if (base > 0) (ddAmount / base * 100) else 0.0

            Card(
                modifier = Modifier
                    .align(Alignment.TopCenter)
                    .padding(top = 4.dp),
                shape = RoundedCornerShape(8.dp),
                colors = CardDefaults.cardColors(containerColor = Color(0xDD0D111A)),
                border = BorderStroke(1.dp, ColorRed.copy(alpha = 0.5f))
            ) {
                Column(
                    modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
                    horizontalAlignment = Alignment.CenterHorizontally
                ) {
                    if (currentSel == 0) {
                        Text(
                            text = "🏁 SESSION OPEN",
                            color = ColorRed,
                            fontSize = 9.sp,
                            fontWeight = FontWeight.Black,
                            fontFamily = FontFamily.Monospace
                        )
                        Text(
                            text = "Drawdown: Rs. 0 (0.00%)",
                            color = ColorText,
                            fontSize = 10.sp,
                            fontWeight = FontWeight.Bold,
                            fontFamily = FontFamily.Monospace
                        )
                    } else {
                        // Chronological index in trades list: currentSel - 1
                        val tradeIdx = currentSel - 1
                        val trade = if (tradeIdx in trades.indices) trades[tradeIdx] else null
                        if (trade != null) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Box(
                                    modifier = Modifier
                                        .clip(RoundedCornerShape(3.dp))
                                        .background(if (trade.direction == "BUY") ColorBlue.copy(alpha = 0.15f) else ColorGold.copy(alpha = 0.15f))
                                        .border(0.5.dp, if (trade.direction == "BUY") ColorBlue else ColorGold, RoundedCornerShape(3.dp))
                                        .padding(horizontal = 4.dp, vertical = 1.dp)
                                ) {
                                    Text(
                                        text = trade.direction,
                                        fontSize = 8.sp,
                                        fontWeight = FontWeight.Black,
                                        fontFamily = FontFamily.Monospace,
                                        color = if (trade.direction == "BUY") ColorBlue else ColorGold
                                    )
                                }
                                Spacer(modifier = Modifier.width(6.dp))
                                Text(
                                    text = "${trade.strike}${trade.opt} @ ${trade.exitTime}",
                                    color = ColorMuted,
                                    fontSize = 8.sp,
                                    fontFamily = FontFamily.Monospace
                                )
                            }
                            Spacer(modifier = Modifier.height(2.dp))
                            Row {
                                Text(
                                    text = String.format("Drawdown: Rs.%,.0f (%.2f%%)", ddAmount, ddPct),
                                    color = ColorRed,
                                    fontSize = 9.sp,
                                    fontWeight = FontWeight.Bold,
                                    fontFamily = FontFamily.Monospace
                                )
                                Spacer(modifier = Modifier.width(8.dp))
                                Text(
                                    text = String.format("Capital: Rs.%,.0f", cap),
                                    color = ColorText,
                                    fontSize = 9.sp,
                                    fontWeight = FontWeight.Bold,
                                    fontFamily = FontFamily.Monospace
                                )
                            }
                            Spacer(modifier = Modifier.height(2.dp))
                            Row {
                                Text(
                                    text = String.format("Trade P&L: Rs.%,.2f", trade.pnl),
                                    color = if (trade.pnl >= 0) ColorGreen else ColorRed,
                                    fontSize = 9.sp,
                                    fontWeight = FontWeight.Bold,
                                    fontFamily = FontFamily.Monospace
                                )
                                Spacer(modifier = Modifier.width(8.dp))
                                Text(
                                    text = "Exit: ${trade.reason.replace('_', ' ')}",
                                    color = ColorText,
                                    fontSize = 9.sp,
                                    fontWeight = FontWeight.Bold,
                                    fontFamily = FontFamily.Monospace
                                )
                            }
                        } else {
                            Text(
                                text = String.format("Drawdown: Rs.%,.0f (%.2f%%)", ddAmount, ddPct),
                                color = ColorRed,
                                fontSize = 9.sp,
                                fontWeight = FontWeight.Bold,
                                fontFamily = FontFamily.Monospace
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun InteractiveChartCard(tradingData: TradingData) {
    var selectedChartIndex by remember { mutableStateOf(0) }
    var dropdownExpanded by remember { mutableStateOf(false) }
    val chartTypes = listOf("📈 EQUITY CURVE", "📊 WIN/LOSS SEQUENTIAL", "📉 DRAWDOWN TRAJECTORY")

    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 8.dp),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = ColorCardBG),
        border = BorderStroke(1.dp, ColorBorder)
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = "📊 VISUALIZATIONS",
                    color = ColorText,
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.Black,
                    fontFamily = FontFamily.Monospace,
                    letterSpacing = 1.sp
                )
                
                Box {
                    Button(
                        onClick = { dropdownExpanded = true },
                        colors = ButtonDefaults.buttonColors(
                            containerColor = Color(0x2200F0FF),
                            contentColor = ColorBlue
                        ),
                        shape = RoundedCornerShape(8.dp),
                        contentPadding = PaddingValues(horizontal = 12.dp, vertical = 6.dp),
                        modifier = Modifier.height(32.dp),
                        border = BorderStroke(1.dp, ColorBlue.copy(alpha = 0.4f))
                    ) {
                        Text(
                            text = chartTypes[selectedChartIndex],
                            fontSize = 10.sp,
                            fontWeight = FontWeight.Bold,
                            fontFamily = FontFamily.Monospace
                        )
                    }
                    
                    DropdownMenu(
                        expanded = dropdownExpanded,
                        onDismissRequest = { dropdownExpanded = false },
                        modifier = Modifier.background(Color(0xFF0D111A))
                    ) {
                        chartTypes.forEachIndexed { index, name ->
                            DropdownMenuItem(
                                text = {
                                    Text(
                                        text = name,
                                        color = if (selectedChartIndex == index) ColorBlue else ColorText,
                                        fontSize = 11.sp,
                                        fontFamily = FontFamily.Monospace,
                                        fontWeight = if (selectedChartIndex == index) FontWeight.Bold else FontWeight.Normal
                                    )
                                },
                                onClick = {
                                    selectedChartIndex = index
                                    dropdownExpanded = false
                                }
                            )
                        }
                    }
                }
            }

            Spacer(modifier = Modifier.height(16.dp))

            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(180.dp)
                    .background(Color(0xFF090D1A).copy(alpha = 0.5f), shape = RoundedCornerShape(12.dp))
                    .border(BorderStroke(1.dp, ColorBorder.copy(alpha = 0.5f)), shape = RoundedCornerShape(12.dp))
                    .padding(12.dp),
                contentAlignment = Alignment.Center
            ) {
                when (selectedChartIndex) {
                    0 -> DrawEquityCurve(tradingData.equity, tradingData.trades, tradingData.stats.baseCapital)
                    1 -> DrawWinLossChart(tradingData.trades)
                    2 -> DrawDrawdownChart(tradingData.equity, tradingData.stats.baseCapital, tradingData.trades)
                }
            }
        }
    }
}

@Composable
fun StatsSection(stats: Stats) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = ColorCardBG),
        border = BorderStroke(1.dp, ColorBorder)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            // Circular Doughnut Win/Loss Ring Canvas
            Box(
                modifier = Modifier
                    .size(80.dp)
                    .padding(4.dp),
                contentAlignment = Alignment.Center
            ) {
                Canvas(modifier = Modifier.fillMaxSize()) {
                    val sweep = if (stats.totalTrades > 0) (stats.winRate / 100.0 * 360f).toFloat() else 0f
                    // Draw Loss background arc
                    drawArc(
                        color = ColorRed.copy(alpha = 0.3f),
                        startAngle = 0f,
                        sweepAngle = 360f,
                        useCenter = false,
                        style = Stroke(width = 7.dp.toPx())
                    )
                    // Draw Win sweep arc
                    drawArc(
                        color = ColorGreen,
                        startAngle = -90f,
                        sweepAngle = sweep,
                        useCenter = false,
                        style = Stroke(width = 7.dp.toPx(), cap = StrokeCap.Round)
                    )
                }
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(
                        text = String.format("%.0f%%", stats.winRate),
                        style = MaterialTheme.typography.titleMedium,
                        color = ColorText,
                        fontWeight = FontWeight.Black,
                        fontFamily = FontFamily.Monospace
                    )
                    Text(
                        text = "WINS",
                        style = MaterialTheme.typography.labelSmall,
                        color = ColorMuted,
                        fontSize = 8.sp,
                        fontFamily = FontFamily.Monospace
                    )
                }
            }

            Spacer(modifier = Modifier.width(20.dp))

            // Right statistics details grid
            Column(modifier = Modifier.weight(1f)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Column {
                        Text(text = "AVG WIN", style = MaterialTheme.typography.labelSmall, color = ColorMuted, fontSize = 9.sp, fontFamily = FontFamily.Monospace)
                        Text(
                            text = String.format("Rs. %,.0f", stats.avgWin),
                            style = MaterialTheme.typography.bodyMedium,
                            color = ColorGreen,
                            fontWeight = FontWeight.Bold,
                            fontFamily = FontFamily.Monospace
                        )
                    }
                    Column(horizontalAlignment = Alignment.End) {
                        Text(text = "AVG LOSS", style = MaterialTheme.typography.labelSmall, color = ColorMuted, fontSize = 9.sp, fontFamily = FontFamily.Monospace)
                        Text(
                            text = String.format("Rs. %,.0f", stats.avgLoss),
                            style = MaterialTheme.typography.bodyMedium,
                            color = ColorRed,
                            fontWeight = FontWeight.Bold,
                            fontFamily = FontFamily.Monospace
                        )
                    }
                }
                Spacer(modifier = Modifier.height(10.dp))
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Column {
                        Text(text = "RISK/REWARD", style = MaterialTheme.typography.labelSmall, color = ColorMuted, fontSize = 9.sp, fontFamily = FontFamily.Monospace)
                        Text(
                            text = String.format("1 : %.2f", stats.rr),
                            style = MaterialTheme.typography.bodyMedium,
                            color = ColorText,
                            fontWeight = FontWeight.Bold,
                            fontFamily = FontFamily.Monospace
                        )
                    }
                    Column(horizontalAlignment = Alignment.End) {
                        Text(text = "TOTAL TAXES", style = MaterialTheme.typography.labelSmall, color = ColorMuted, fontSize = 9.sp, fontFamily = FontFamily.Monospace)
                        Text(
                            text = String.format("Rs. %,.2f", stats.totalCharges),
                            style = MaterialTheme.typography.bodyMedium,
                            color = ColorGold,
                            fontWeight = FontWeight.Bold,
                            fontFamily = FontFamily.Monospace
                        )
                    }
                }
            }
        }
    }
}

@Composable
fun SmartSignalGuardCard(
    status: SmartStatus,
    onToggleChange: (Boolean) -> Unit
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 8.dp),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = ColorCardBG),
        border = BorderStroke(1.5.dp, ColorAccent.copy(alpha = 0.35f))
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        text = "🛡️",
                        fontSize = 18.sp
                    )
                    Spacer(modifier = Modifier.width(8.dp))
                    Text(
                        text = "SMART SIGNAL GUARD",
                        color = ColorText,
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Black,
                        fontFamily = FontFamily.Monospace,
                        letterSpacing = 1.sp
                    )
                }
                
                Switch(
                    checked = status.enabled,
                    onCheckedChange = onToggleChange,
                    colors = SwitchDefaults.colors(
                        checkedThumbColor = ColorGreen,
                        checkedTrackColor = ColorGreen.copy(alpha = 0.3f),
                        uncheckedThumbColor = ColorMuted,
                        uncheckedTrackColor = ColorBorder
                    )
                )
            }
            
            Spacer(modifier = Modifier.height(16.dp))
            
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                Box(
                    modifier = Modifier
                        .weight(1f)
                        .background(Color(0xFF090D1A).copy(alpha = 0.5f), shape = RoundedCornerShape(8.dp))
                        .border(BorderStroke(1.dp, ColorBorder.copy(alpha = 0.4f)), shape = RoundedCornerShape(8.dp))
                        .padding(8.dp),
                    contentAlignment = Alignment.Center
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text(
                            text = "STATUS",
                            color = ColorMuted,
                            fontSize = 8.sp,
                            fontWeight = FontWeight.Bold,
                            fontFamily = FontFamily.Monospace
                        )
                        Spacer(modifier = Modifier.height(4.dp))
                        Text(
                            text = status.status,
                            color = if (status.enabled) ColorGreen else ColorRed,
                            fontSize = 10.sp,
                            fontWeight = FontWeight.Black,
                            fontFamily = FontFamily.Monospace,
                            textAlign = TextAlign.Center
                        )
                    }
                }
                
                Box(
                    modifier = Modifier
                        .weight(1f)
                        .background(Color(0xFF090D1A).copy(alpha = 0.5f), shape = RoundedCornerShape(8.dp))
                        .border(BorderStroke(1.dp, ColorBorder.copy(alpha = 0.4f)), shape = RoundedCornerShape(8.dp))
                        .padding(8.dp),
                    contentAlignment = Alignment.Center
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text(
                            text = "ACCURACY",
                            color = ColorMuted,
                            fontSize = 8.sp,
                            fontWeight = FontWeight.Bold,
                            fontFamily = FontFamily.Monospace
                        )
                        Spacer(modifier = Modifier.height(4.dp))
                        Text(
                            text = status.accuracy,
                            color = ColorGold,
                            fontSize = 11.sp,
                            fontWeight = FontWeight.Black,
                            fontFamily = FontFamily.Monospace
                        )
                    }
                }
            }
            
            Spacer(modifier = Modifier.height(8.dp))
            
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                Box(
                    modifier = Modifier
                        .weight(1f)
                        .background(Color(0xFF090D1A).copy(alpha = 0.5f), shape = RoundedCornerShape(8.dp))
                        .border(BorderStroke(1.dp, ColorBorder.copy(alpha = 0.4f)), shape = RoundedCornerShape(8.dp))
                        .padding(8.dp),
                    contentAlignment = Alignment.Center
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text(
                            text = "SAMPLES",
                            color = ColorMuted,
                            fontSize = 8.sp,
                            fontWeight = FontWeight.Bold,
                            fontFamily = FontFamily.Monospace
                        )
                        Spacer(modifier = Modifier.height(4.dp))
                        Text(
                            text = "${status.totalSamples}",
                            color = ColorBlue,
                            fontSize = 11.sp,
                            fontWeight = FontWeight.Black,
                            fontFamily = FontFamily.Monospace
                        )
                    }
                }
                
                Box(
                    modifier = Modifier
                        .weight(1f)
                        .background(Color(0xFF090D1A).copy(alpha = 0.5f), shape = RoundedCornerShape(8.dp))
                        .border(BorderStroke(1.dp, ColorBorder.copy(alpha = 0.4f)), shape = RoundedCornerShape(8.dp))
                        .padding(8.dp),
                    contentAlignment = Alignment.Center
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text(
                            text = "WIN / LOSS",
                            color = ColorMuted,
                            fontSize = 8.sp,
                            fontWeight = FontWeight.Bold,
                            fontFamily = FontFamily.Monospace
                        )
                        Spacer(modifier = Modifier.height(4.dp))
                        Text(
                            text = "${status.wins}W / ${status.losses}L",
                            color = ColorGreen,
                            fontSize = 11.sp,
                            fontWeight = FontWeight.Black,
                            fontFamily = FontFamily.Monospace
                        )
                    }
                }
                
                Box(
                    modifier = Modifier
                        .weight(1f)
                        .background(Color(0xFF090D1A).copy(alpha = 0.5f), shape = RoundedCornerShape(8.dp))
                        .border(BorderStroke(1.dp, ColorBorder.copy(alpha = 0.4f)), shape = RoundedCornerShape(8.dp))
                        .padding(8.dp),
                    contentAlignment = Alignment.Center
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text(
                            text = "BLOCKED",
                            color = ColorMuted,
                            fontSize = 8.sp,
                            fontWeight = FontWeight.Bold,
                            fontFamily = FontFamily.Monospace
                        )
                        Spacer(modifier = Modifier.height(4.dp))
                        Text(
                            text = "${status.filteredCount}",
                            color = ColorRed,
                            fontSize = 11.sp,
                            fontWeight = FontWeight.Black,
                            fontFamily = FontFamily.Monospace
                        )
                    }
                }
            }
        }
    }
}

@Composable
fun LiveLogConsoleCard(logs: List<String>) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 8.dp),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = ColorCardBG),
        border = BorderStroke(1.dp, ColorBorder)
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Text(
                text = "📋 SYSTEM TERMINAL LOGS",
                color = ColorBlue,
                style = MaterialTheme.typography.labelSmall.copy(fontSize = 10.sp),
                fontWeight = FontWeight.Bold,
                fontFamily = FontFamily.Monospace,
                letterSpacing = 1.sp
            )
            
            Spacer(modifier = Modifier.height(12.dp))
            
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(180.dp)
                    .background(Color(0xFF090D1A), shape = RoundedCornerShape(12.dp))
                    .border(BorderStroke(1.5.dp, ColorBorder.copy(alpha = 0.3f)), shape = RoundedCornerShape(12.dp))
                    .padding(8.dp)
            ) {
                if (logs.isEmpty()) {
                    Box(
                        modifier = Modifier.fillMaxSize(),
                        contentAlignment = Alignment.Center
                    ) {
                        Text(
                            text = "NO SYSTEM LOG FEED RECEIVED",
                            color = ColorMuted,
                            fontFamily = FontFamily.Monospace,
                            fontSize = 10.sp
                        )
                    }
                } else {
                    LazyColumn(
                        modifier = Modifier.fillMaxSize()
                    ) {
                        items(logs) { line ->
                            val color = when {
                                line.contains("WIN") || line.contains("TARGET") || line.contains("ENTRY") -> ColorGreen
                                line.contains("LOSS") || line.contains("STOP") || line.contains("REJECTED") -> ColorRed
                                line.contains("STR") || line.contains("DATA") || line.contains("SYSTEM") || line.contains("LTP") -> ColorBlue
                                else -> ColorText
                            }
                            Text(
                                text = line,
                                color = color,
                                fontFamily = FontFamily.Monospace,
                                fontSize = 9.5.sp,
                                modifier = Modifier.padding(vertical = 2.dp)
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun PositionCard(
    position: Position,
    onEditClick: () -> Unit
) {
    val isBuy   = position.direction == "BUY"
    val isProfit = position.pnl >= 0
    val pnlColor  = if (isProfit) ColorGreen else ColorRed
    val dirColor  = if (isBuy) Color(0xFF00CFFF) else Color(0xFFFF6B35)
    val glowColor = if (isProfit) ColorGreen.copy(alpha = 0.25f) else ColorRed.copy(alpha = 0.25f)

    // Pulsing animation for the live dot
    val infinitePulse = rememberInfiniteTransition(label = "pos_pulse")
    val pulseAlpha by infinitePulse.animateFloat(
        initialValue = 0.3f, targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(900, easing = FastOutSlowInEasing), RepeatMode.Reverse),
        label = "pulse_alpha"
    )
    val pulseScale by infinitePulse.animateFloat(
        initialValue = 0.85f, targetValue = 1.15f,
        animationSpec = infiniteRepeatable(tween(900, easing = FastOutSlowInEasing), RepeatMode.Reverse),
        label = "pulse_scale"
    )

    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = ColorCardBG),
        border = BorderStroke(1.5.dp, glowColor)
    ) {
        Column(
            modifier = Modifier
                .background(
                    Brush.verticalGradient(
                        listOf(
                            if (isProfit) Color(0xFF003320) else Color(0xFF330010),
                            ColorCardBG
                        )
                    )
                )
                .padding(16.dp)
        ) {

            // ── ROW 1: Direction badge + strike + live dot + PnL ──
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {

                    // Live pulsing dot
                    Box(
                        modifier = Modifier
                            .size(9.dp)
                            .graphicsLayer(scaleX = pulseScale, scaleY = pulseScale, alpha = pulseAlpha)
                            .clip(RoundedCornerShape(5.dp))
                            .background(if (isProfit) ColorGreen else ColorRed)
                    )
                    Spacer(modifier = Modifier.width(8.dp))

                    // Direction badge: ▲ BUY or ▼ SELL
                    Box(
                        modifier = Modifier
                            .clip(RoundedCornerShape(6.dp))
                            .background(
                                Brush.horizontalGradient(
                                    if (isBuy)
                                        listOf(Color(0xFF003C5E), Color(0xFF005A8E))
                                    else
                                        listOf(Color(0xFF5E1A00), Color(0xFF8E2E00))
                                )
                            )
                            .border(1.dp, dirColor.copy(alpha = 0.6f), RoundedCornerShape(6.dp))
                            .padding(horizontal = 8.dp, vertical = 3.dp)
                    ) {
                        Text(
                            text = if (isBuy) "▲ BUY" else "▼ SELL",
                            color = dirColor,
                            fontSize = 10.sp,
                            fontWeight = FontWeight.Black,
                            fontFamily = FontFamily.Monospace,
                            letterSpacing = 0.5.sp
                        )
                    }
                    Spacer(modifier = Modifier.width(8.dp))

                    // Index + Strike + Option type with CE=cyan / PE=orange
                    val optColor = if (position.opt == "CE") Color(0xFF00E5FF) else Color(0xFFFF9800)
                    Row(verticalAlignment = Alignment.Bottom) {
                        Text(
                            text = "${position.index} ${position.strike.toInt()}",
                            color = ColorText,
                            fontSize = 13.sp,
                            fontWeight = FontWeight.Bold,
                            fontFamily = FontFamily.Monospace
                        )
                        Spacer(modifier = Modifier.width(4.dp))
                        Box(
                            modifier = Modifier
                                .clip(RoundedCornerShape(4.dp))
                                .background(optColor.copy(alpha = 0.15f))
                                .border(1.dp, optColor.copy(alpha = 0.5f), RoundedCornerShape(4.dp))
                                .padding(horizontal = 5.dp, vertical = 1.dp)
                        ) {
                            Text(
                                text = position.opt,
                                color = optColor,
                                fontSize = 9.sp,
                                fontWeight = FontWeight.Black,
                                fontFamily = FontFamily.Monospace
                            )
                        }
                    }
                }

                // PnL with ▲/▼ arrow
                Column(horizontalAlignment = Alignment.End) {
                    Text(
                        text = "${if (isProfit) "▲" else "▼"} ${String.format("%+,.2f", position.pnl)}",
                        color = pnlColor,
                        fontSize = 15.sp,
                        fontWeight = FontWeight.Black,
                        fontFamily = FontFamily.Monospace
                    )
                    Text(
                        text = "${String.format("%+.2f", position.pnlPct)}%",
                        color = pnlColor.copy(alpha = 0.7f),
                        fontSize = 10.sp,
                        fontFamily = FontFamily.Monospace
                    )
                }
            }

            Spacer(modifier = Modifier.height(12.dp))

            // ── ROW 2: Entry / Current / SL-TP ──
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                listOf(
                    "ENTRY" to String.format("%.1f", position.entry),
                    "CURRENT" to String.format("%.1f", position.cur),
                    "SL" to String.format("%.1f", position.sl),
                    "TARGET" to String.format("%.1f", position.tp)
                ).forEach { (label, value) ->
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text(
                            text = label,
                            color = ColorMuted,
                            fontSize = 8.sp,
                            fontFamily = FontFamily.Monospace,
                            letterSpacing = 0.5.sp
                        )
                        Text(
                            text = value,
                            color = ColorText,
                            fontSize = 11.sp,
                            fontFamily = FontFamily.Monospace,
                            fontWeight = FontWeight.Bold
                        )
                    }
                }
            }

            Spacer(modifier = Modifier.height(10.dp))

            // ── Divider ──
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(1.dp)
                    .background(
                        Brush.horizontalGradient(
                            listOf(Color.Transparent, ColorBorder, Color.Transparent)
                        )
                    )
            )
            Spacer(modifier = Modifier.height(10.dp))

            // ── ROW 3: TSL dot + Charges + Edit button ──
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                // TSL state
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        text = if (position.trailingSlEnabled) "🚀" else "🔒",
                        fontSize = 12.sp
                    )
                    Spacer(modifier = Modifier.width(5.dp))
                    Text(
                        text = "TSL ${if (position.trailingSlEnabled) "ON" else "OFF"}",
                        color = if (position.trailingSlEnabled) ColorGreen else ColorMuted,
                        fontSize = 9.sp,
                        fontWeight = FontWeight.Bold,
                        fontFamily = FontFamily.Monospace
                    )
                    Spacer(modifier = Modifier.width(12.dp))
                    Text(
                        text = "⚡ Rs.${String.format("%.2f", position.charges)}",
                        color = ColorMuted,
                        fontSize = 9.sp,
                        fontFamily = FontFamily.Monospace
                    )
                }

                // Edit button — neon gradient border
                Button(
                    onClick = onEditClick,
                    modifier = Modifier.height(28.dp),
                    contentPadding = PaddingValues(horizontal = 12.dp, vertical = 0.dp),
                    shape = RoundedCornerShape(8.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = Color.Transparent),
                    border = BorderStroke(
                        1.5.dp,
                        Brush.horizontalGradient(listOf(Color(0xFF00E5FF), Color(0xFF9C27B0)))
                    )
                ) {
                    Text(
                        text = "✏ EDIT",
                        color = Color(0xFF00E5FF),
                        fontSize = 10.sp,
                        fontWeight = FontWeight.Black,
                        fontFamily = FontFamily.Monospace,
                        letterSpacing = 1.sp
                    )
                }
            }
        }
    }
}

@Composable
fun TradeHistoryCard(trade: Trade) {
    val isWin = trade.pnl >= 0
    val pnlColor = if (isWin) ColorGreen else ColorRed

    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(10.dp),
        colors = CardDefaults.cardColors(containerColor = ColorCardBG),
        border = BorderStroke(1.dp, ColorBorder)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        text = trade.direction,
                        color = if (trade.direction == "BUY") ColorBlue else ColorGold,
                        style = MaterialTheme.typography.labelSmall,
                        fontWeight = FontWeight.Black,
                        fontFamily = FontFamily.Monospace
                    )
                    Spacer(modifier = Modifier.width(6.dp))
                    Text(
                        text = "${trade.strike} ${trade.opt}",
                        color = ColorText,
                        style = MaterialTheme.typography.bodySmall,
                        fontWeight = FontWeight.Bold,
                        fontFamily = FontFamily.Monospace
                    )
                }
                Spacer(modifier = Modifier.height(4.dp))
                Text(
                    text = "${trade.date} ${trade.entryTime} → ${trade.exitTime} | Exit: ${trade.reason.replace('_', ' ')}",
                    color = ColorMuted,
                    style = MaterialTheme.typography.bodySmall,
                    fontSize = 9.sp,
                    fontFamily = FontFamily.Monospace
                )
            }

            Spacer(modifier = Modifier.width(12.dp))

            Column(horizontalAlignment = Alignment.End) {
                Text(
                    text = String.format("Rs. %,+.2f", trade.pnl),
                    color = pnlColor,
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.Black,
                    fontFamily = FontFamily.Monospace
                )
                Text(
                    text = String.format("Taxes: Rs.%,.2f", trade.charges),
                    color = ColorMuted,
                    style = MaterialTheme.typography.bodySmall,
                    fontSize = 9.sp,
                    fontFamily = FontFamily.Monospace
                )
            }
        }
    }
}

// =============================================================================
// API & NETWORK UTILITIES (STANDARD HTTP & JSON PARSING)
// =============================================================================

fun buildUrl(baseUrl: String, path: String): URL {
    val base = if (baseUrl.contains("?")) baseUrl.substringBefore("?") else baseUrl
    val query = if (baseUrl.contains("?")) "?" + baseUrl.substringAfter("?") else ""
    val cleanBase = base.removeSuffix("/")
    val cleanPath = if (path.startsWith("/")) path else "/$path"
    return URL("$cleanBase$cleanPath$query")
}

suspend fun fetchDashboardData(baseUrl: String): TradingData = withContext(Dispatchers.IO) {
    val url = buildUrl(baseUrl, "/api/data")
    val conn = url.openConnection() as HttpURLConnection
    conn.requestMethod = "GET"
    conn.connectTimeout = 8000
    conn.readTimeout = 8000

    val responseCode = conn.responseCode
    if (responseCode != HttpURLConnection.HTTP_OK) {
        throw Exception("Server returned HTTP error $responseCode")
    }

    val reader = BufferedReader(InputStreamReader(conn.inputStream))
    val sb = StringBuilder()
    var line: String?
    while (reader.readLine().also { line = it } != null) {
        sb.append(line)
    }
    reader.close()
    conn.disconnect()

    val root = JSONObject(sb.toString())
    val statsObj = root.optJSONObject("stats") ?: JSONObject()

    val stats = Stats(
        capital = statsObj.optDouble("capital", 0.0),
        baseCapital = statsObj.optDouble("base_capital", 0.0),
        totalPnl = statsObj.optDouble("total_pnl", 0.0),
        totalTrades = statsObj.optInt("total_trades", 0),
        wins = statsObj.optInt("wins", 0),
        losses = statsObj.optInt("losses", 0),
        winRate = statsObj.optDouble("win_rate", 0.0),
        avgWin = statsObj.optDouble("avg_win", 0.0),
        avgLoss = statsObj.optDouble("avg_loss", 0.0),
        rr = statsObj.optDouble("rr", 0.0),
        best = statsObj.optDouble("best", 0.0),
        worst = statsObj.optDouble("worst", 0.0),
        totalCharges = statsObj.optDouble("total_charges", 0.0)
    )

    val tradesArray = root.optJSONArray("trades") ?: JSONArray()
    val trades = mutableListOf<Trade>()
    for (i in 0 until tradesArray.length()) {
        val obj = tradesArray.optJSONObject(i) ?: continue
        trades.add(
            Trade(
                tid = obj.optString("tid", ""),
                date = obj.optString("date", ""),
                entryTime = obj.optString("entry_time", ""),
                exitTime = obj.optString("exit_time", ""),
                direction = obj.optString("direction", ""),
                strike = obj.optString("strike", ""),
                opt = obj.optString("opt", ""),
                entry = obj.optDouble("entry", 0.0),
                exit = obj.optDouble("exit", 0.0),
                pnl = obj.optDouble("pnl", 0.0),
                reason = obj.optString("reason", ""),
                charges = obj.optDouble("charges", 0.0)
            )
        )
    }

    val openArray = root.optJSONArray("open_positions") ?: JSONArray()
    val openPositions = mutableListOf<Position>()
    for (i in 0 until openArray.length()) {
        val obj = openArray.optJSONObject(i) ?: continue
        openPositions.add(
            Position(
                tid = obj.optString("tid", ""),
                index = obj.optString("index", ""),
                direction = obj.optString("direction", ""),
                strike = obj.optDouble("strike", 0.0),
                opt = obj.optString("opt", ""),
                contracts = obj.optInt("contracts", 0),
                entry = obj.optDouble("entry", 0.0),
                sl = obj.optDouble("sl", 0.0),
                tp = obj.optDouble("tp", 0.0),
                cur = obj.optDouble("cur", 0.0),
                pnl = obj.optDouble("pnl", 0.0),
                pnlPct = obj.optDouble("pnl_pct", 0.0),
                charges = obj.optDouble("charges", 0.0),
                entryTime = obj.optString("entry_time", ""),
                trailingSlEnabled = obj.optBoolean("trailing_sl_enabled", false)
            )
        )
    }

    val dhanCred = root.optJSONObject("dhan_credentials") ?: JSONObject()
    val growwCred = root.optJSONObject("groww_credentials") ?: JSONObject()

    val equityArray = root.optJSONArray("equity") ?: JSONArray()
    val equity = mutableListOf<Double>()
    for (i in 0 until equityArray.length()) {
        equity.add(equityArray.optDouble(i, 0.0))
    }

    val indicesArray = root.optJSONArray("trading_indices") ?: JSONArray()
    val tradingIndices = mutableListOf<String>()
    for (i in 0 until indicesArray.length()) {
        tradingIndices.add(indicesArray.optString(i))
    }

    val smartObj = root.optJSONObject("smart_status") ?: JSONObject()
    val smartStatus = SmartStatus(
        enabled = smartObj.optBoolean("enabled", false),
        status = smartObj.optString("status", "✏️ LEARNING MODE"),
        accuracy = smartObj.optString("accuracy", "—"),
        totalSamples = smartObj.optInt("total_samples", 0),
        wins = smartObj.optInt("wins", 0),
        losses = smartObj.optInt("losses", 0),
        filteredCount = smartObj.optInt("filtered_count", 0),
        threshold = smartObj.optString("threshold", "50%")
    )

    val logArray = root.optJSONArray("log") ?: JSONArray()
    val logList = mutableListOf<String>()
    for (i in 0 until logArray.length()) {
        logList.add(logArray.optString(i))
    }

    TradingData(
        niftySpot = root.optDouble("nifty_spot", 0.0),
        sensexSpot = root.optDouble("sensex_spot", 0.0),
        vix = root.optDouble("vix", 15.0),
        trades = trades,
        openPositions = openPositions,
        stats = stats,
        running = root.optBoolean("running", false),
        liveTrading = root.optBoolean("live_trading", false),
        activeBroker = root.optString("active_broker", "DHAN"),
        smartFilterEnabled = smartStatus.enabled,
        trailingSlEnabled = root.optBoolean("trailing_sl_enabled", false),
        dhanClientId = dhanCred.optString("client_id", ""),
        dhanHasToken = dhanCred.optBoolean("has_token", false),
        growwClientId = growwCred.optString("client_id", ""),
        growwHasToken = growwCred.optBoolean("has_token", false),
        equity = equity,
        tradingIndices = tradingIndices,
        smartStatus = smartStatus,
        log = logList
    )
}

suspend fun updatePositionLimits(
    baseUrl: String,
    tid: String,
    sl: Double,
    tp: Double,
    tsl: Boolean
): Boolean = withContext(Dispatchers.IO) {
    try {
        val url = buildUrl(baseUrl, "/api/position/update")
        val conn = url.openConnection() as HttpURLConnection
        conn.requestMethod = "POST"
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
        conn.doOutput = true
        conn.connectTimeout = 8000
        conn.readTimeout = 8000

        val payload = JSONObject().apply {
            put("tid", tid)
            put("sl", sl)
            put("tp", tp)
            put("tsl", tsl)
        }

        val writer = OutputStreamWriter(conn.outputStream)
        writer.write(payload.toString())
        writer.flush()
        writer.close()

        val responseCode = conn.responseCode
        conn.disconnect()
        responseCode == HttpURLConnection.HTTP_OK
    } catch (e: Exception) {
        false
    }
}

suspend fun toggleSmartFilter(baseUrl: String, enabled: Boolean): Boolean = withContext(Dispatchers.IO) {
    try {
        val url = buildUrl(baseUrl, "/api/smart_filter/toggle")
        val conn = url.openConnection() as HttpURLConnection
        conn.requestMethod = "POST"
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
        conn.doOutput = true
        conn.connectTimeout = 5000
        conn.readTimeout = 5000

        val payload = JSONObject().apply {
            put("enabled", enabled)
        }

        val writer = OutputStreamWriter(conn.outputStream)
        writer.write(payload.toString())
        writer.flush()
        writer.close()

        val responseCode = conn.responseCode
        conn.disconnect()
        responseCode == HttpURLConnection.HTTP_OK
    } catch (e: Exception) {
        false
    }
}

suspend fun toggleTrailingSl(baseUrl: String, enabled: Boolean): Boolean = withContext(Dispatchers.IO) {
    try {
        val url = buildUrl(baseUrl, "/api/trailing_sl/toggle")
        val conn = url.openConnection() as HttpURLConnection
        conn.requestMethod = "POST"
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
        conn.doOutput = true
        conn.connectTimeout = 5000
        conn.readTimeout = 5000

        val payload = JSONObject().apply {
            put("enabled", enabled)
        }

        val writer = OutputStreamWriter(conn.outputStream)
        writer.write(payload.toString())
        writer.flush()
        writer.close()

        val responseCode = conn.responseCode
        conn.disconnect()
        responseCode == HttpURLConnection.HTTP_OK
    } catch (e: Exception) {
        false
    }
}

suspend fun setTradingMode(baseUrl: String, isLive: Boolean): Boolean = withContext(Dispatchers.IO) {
    try {
        val url = buildUrl(baseUrl, "/api/mode")
        val conn = url.openConnection() as HttpURLConnection
        conn.requestMethod = "POST"
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
        conn.doOutput = true
        conn.connectTimeout = 5000
        conn.readTimeout = 5000

        val payload = JSONObject().apply {
            put("mode", if (isLive) "LIVE" else "DEMO")
        }

        val writer = OutputStreamWriter(conn.outputStream)
        writer.write(payload.toString())
        writer.flush()
        writer.close()

        val responseCode = conn.responseCode
        conn.disconnect()
        responseCode == HttpURLConnection.HTTP_OK
    } catch (e: Exception) {
        false
    }
}

suspend fun setActiveBroker(baseUrl: String, broker: String): Boolean = withContext(Dispatchers.IO) {
    try {
        val url = buildUrl(baseUrl, "/api/broker")
        val conn = url.openConnection() as HttpURLConnection
        conn.requestMethod = "POST"
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
        conn.doOutput = true
        conn.connectTimeout = 5000
        conn.readTimeout = 5000

        val payload = JSONObject().apply {
            put("broker", broker.uppercase())
        }

        val writer = OutputStreamWriter(conn.outputStream)
        writer.write(payload.toString())
        writer.flush()
        writer.close()

        val responseCode = conn.responseCode
        conn.disconnect()
        responseCode == HttpURLConnection.HTTP_OK
    } catch (e: Exception) {
        false
    }
}

suspend fun controlBot(baseUrl: String, start: Boolean): Boolean = withContext(Dispatchers.IO) {
    try {
        val endpoint = if (start) "start" else "stop"
        val url = buildUrl(baseUrl, "/api/$endpoint")
        val conn = url.openConnection() as HttpURLConnection
        conn.requestMethod = "POST"
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
        conn.doOutput = true
        conn.connectTimeout = 5000
        conn.readTimeout = 5000

        val writer = OutputStreamWriter(conn.outputStream)
        writer.write("{}")
        writer.flush()
        writer.close()

        val responseCode = conn.responseCode
        conn.disconnect()
        responseCode == HttpURLConnection.HTTP_OK
    } catch (e: Exception) {
        false
    }
}

suspend fun submitBrokerCredentials(
    baseUrl: String,
    broker: String,
    clientId: String,
    accessToken: String
): Pair<Boolean, String> = withContext(Dispatchers.IO) {
    try {
        val url = buildUrl(baseUrl, "/api/broker/credentials")
        val conn = url.openConnection() as HttpURLConnection
        conn.requestMethod = "POST"
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
        conn.doOutput = true
        conn.connectTimeout = 8000
        conn.readTimeout = 8000

        val payload = JSONObject().apply {
            put("broker", broker.uppercase())
            put("client_id", clientId)
            put("access_token", accessToken)
        }

        val writer = OutputStreamWriter(conn.outputStream)
        writer.write(payload.toString())
        writer.flush()
        writer.close()

        val responseCode = conn.responseCode
        val stream = if (responseCode == HttpURLConnection.HTTP_OK) conn.inputStream else conn.errorStream
        val reader = BufferedReader(InputStreamReader(stream))
        val sb = StringBuilder()
        var line: String?
        while (reader.readLine().also { line = it } != null) {
            sb.append(line)
        }
        reader.close()
        conn.disconnect()

        val resp = JSONObject(sb.toString())
        val status = resp.optString("status", "error")
        val message = resp.optString("message", "Request failed")
        
        if (responseCode == HttpURLConnection.HTTP_OK && status == "success") {
            Pair(true, message)
        } else {
            Pair(false, message)
        }
    } catch (e: Exception) {
        Pair(false, e.message ?: "Network error connecting to broker API")
    }
}

suspend fun refreshVixOnServer(baseUrl: String): Boolean = withContext(Dispatchers.IO) {
    try {
        val url = buildUrl(baseUrl, "/api/vix/refresh")
        val conn = url.openConnection() as HttpURLConnection
        conn.requestMethod = "POST"
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
        conn.doOutput = true
        conn.connectTimeout = 5000
        conn.readTimeout = 5000
        val writer = OutputStreamWriter(conn.outputStream)
        writer.write("{}")
        writer.flush()
        writer.close()
        val responseCode = conn.responseCode
        conn.disconnect()
        responseCode == HttpURLConnection.HTTP_OK
    } catch (e: Exception) {
        false // Non-critical — main data fetch will still proceed
    }
}

suspend fun updateTradingIndices(baseUrl: String, indices: List<String>): Boolean = withContext(Dispatchers.IO) {
    try {
        val url = buildUrl(baseUrl, "/api/indices/update")
        val conn = url.openConnection() as HttpURLConnection
        conn.requestMethod = "POST"
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
        conn.doOutput = true
        conn.connectTimeout = 5000
        conn.readTimeout = 5000

        val payload = JSONObject().apply {
            val array = JSONArray()
            indices.forEach { array.put(it) }
            put("indices", array)
        }

        val writer = OutputStreamWriter(conn.outputStream)
        writer.write(payload.toString())
        writer.flush()
        writer.close()

        val responseCode = conn.responseCode
        conn.disconnect()
        responseCode == HttpURLConnection.HTTP_OK
    } catch (e: Exception) {
        false
    }
}

suspend fun squareOffAllPositions(baseUrl: String): Boolean = withContext(Dispatchers.IO) {
    try {
        val url = buildUrl(baseUrl, "/api/squareoff")
        val conn = url.openConnection() as HttpURLConnection
        conn.requestMethod = "POST"
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
        conn.doOutput = true
        conn.connectTimeout = 5000
        conn.readTimeout = 5000

        val writer = OutputStreamWriter(conn.outputStream)
        writer.write("{}")
        writer.flush()
        writer.close()

        val responseCode = conn.responseCode
        conn.disconnect()
        responseCode == HttpURLConnection.HTTP_OK
    } catch (e: Exception) {
        false
    }
}

// =============================================================================
// AUTHENTICATION SCREEN & UTILITY
// =============================================================================

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AuthScreen(
    rawBaseUrl: String,
    onUrlChange: (String) -> Unit,
    username: String,
    onUsernameChange: (String) -> Unit,
    onAuthSuccess: () -> Unit
) {
    var password by remember { mutableStateOf("") }
    var isLoginTab by remember { mutableStateOf(true) }
    var errorMessage by remember { mutableStateOf<String?>(null) }
    var infoMessage by remember { mutableStateOf<String?>(null) }
    var isSubmitting by remember { mutableStateOf(false) }
    val coroutineScope = rememberCoroutineScope()

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(ColorBG),
        contentAlignment = Alignment.Center
    ) {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(
                    Brush.radialGradient(
                        colors = listOf(ColorAccent.copy(alpha = 0.25f), Color.Transparent),
                        radius = 600f
                    )
                )
        )

        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(24.dp)
                .verticalScroll(rememberScrollState()),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Logo3D(modifier = Modifier.size(72.dp))
            Spacer(modifier = Modifier.height(12.dp))
            Text(
                text = "ALGO PULSE",
                style = MaterialTheme.typography.headlineMedium.copy(
                    fontWeight = FontWeight.Black,
                    color = Color.White,
                    fontFamily = FontFamily.Monospace,
                    letterSpacing = 2.sp
                )
            )
            Spacer(modifier = Modifier.height(4.dp))
            Text(
                text = "MULTI-TENANT ORCHESTRATOR",
                style = MaterialTheme.typography.labelSmall,
                color = ColorBlue,
                fontWeight = FontWeight.Bold,
                fontFamily = FontFamily.Monospace,
                letterSpacing = 1.5.sp
            )

            Spacer(modifier = Modifier.height(32.dp))

            Card(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(16.dp),
                colors = CardDefaults.cardColors(containerColor = ColorCardBG),
                border = BorderStroke(
                    1.5.dp,
                    Brush.horizontalGradient(listOf(ColorBlue.copy(alpha = 0.8f), ColorAccent.copy(alpha = 0.8f)))
                )
            ) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(20.dp),
                    horizontalAlignment = Alignment.CenterHorizontally
                ) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(40.dp)
                            .clip(RoundedCornerShape(8.dp))
                            .background(Color.White.copy(alpha = 0.05f))
                            .border(1.dp, ColorBorder, RoundedCornerShape(8.dp)),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Box(
                            modifier = Modifier
                                .weight(1f)
                                .fillMaxHeight()
                                .clip(RoundedCornerShape(8.dp))
                                .background(if (isLoginTab) ColorBlue.copy(alpha = 0.2f) else Color.Transparent)
                                .clickable { 
                                    isLoginTab = true 
                                    errorMessage = null
                                    infoMessage = null
                                }
                                .wrapContentSize(Alignment.Center)
                        ) {
                            Text(
                                text = "LOGIN",
                                color = if (isLoginTab) ColorBlue else ColorMuted,
                                fontSize = 12.sp,
                                fontWeight = FontWeight.Bold,
                                fontFamily = FontFamily.Monospace
                            )
                        }
                        Box(
                            modifier = Modifier
                                .weight(1f)
                                .fillMaxHeight()
                                .clip(RoundedCornerShape(8.dp))
                                .background(if (!isLoginTab) ColorBlue.copy(alpha = 0.2f) else Color.Transparent)
                                .clickable { 
                                    isLoginTab = false 
                                    errorMessage = null
                                    infoMessage = null
                                }
                                .wrapContentSize(Alignment.Center)
                        ) {
                            Text(
                                text = "REGISTER",
                                color = if (!isLoginTab) ColorBlue else ColorMuted,
                                fontSize = 12.sp,
                                fontWeight = FontWeight.Bold,
                                fontFamily = FontFamily.Monospace
                            )
                        }
                    }

                    Spacer(modifier = Modifier.height(20.dp))

                    OutlinedTextField(
                        value = rawBaseUrl,
                        onValueChange = onUrlChange,
                        label = { Text("API Backend URL", color = ColorMuted, fontFamily = FontFamily.Monospace) },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                        textStyle = LocalTextStyle.current.copy(color = ColorText, fontFamily = FontFamily.Monospace),
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri)
                    )

                    Spacer(modifier = Modifier.height(12.dp))

                    OutlinedTextField(
                        value = username,
                        onValueChange = onUsernameChange,
                        label = { Text("Username", color = ColorMuted, fontFamily = FontFamily.Monospace) },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                        textStyle = LocalTextStyle.current.copy(color = ColorText, fontFamily = FontFamily.Monospace)
                    )

                    Spacer(modifier = Modifier.height(12.dp))

                    OutlinedTextField(
                        value = password,
                        onValueChange = { password = it },
                        label = { Text("Password", color = ColorMuted, fontFamily = FontFamily.Monospace) },
                        singleLine = true,
                        visualTransformation = PasswordVisualTransformation(),
                        modifier = Modifier.fillMaxWidth(),
                        textStyle = LocalTextStyle.current.copy(color = ColorText, fontFamily = FontFamily.Monospace),
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password)
                    )

                    Spacer(modifier = Modifier.height(16.dp))

                    if (errorMessage != null) {
                        Text(
                            text = errorMessage!!,
                            color = ColorRed,
                            fontSize = 11.sp,
                            fontFamily = FontFamily.Monospace,
                            textAlign = TextAlign.Center,
                            modifier = Modifier.padding(bottom = 12.dp)
                        )
                    }

                    if (infoMessage != null) {
                        Text(
                            text = infoMessage!!,
                            color = ColorGreen,
                            fontSize = 11.sp,
                            fontFamily = FontFamily.Monospace,
                            textAlign = TextAlign.Center,
                            modifier = Modifier.padding(bottom = 12.dp)
                        )
                    }

                    Button(
                        onClick = {
                            if (rawBaseUrl.isEmpty()) {
                                errorMessage = "Backend URL is required"
                                return@Button
                            }
                            if (username.isEmpty() || password.isEmpty()) {
                                errorMessage = "Username and password are required"
                                return@Button
                            }
                            isSubmitting = true
                            errorMessage = null
                            infoMessage = null
                            coroutineScope.launch {
                                val endpoint = if (isLoginTab) "/api/login" else "/api/register"
                                val (success, msg) = performAuth(rawBaseUrl, endpoint, username, password)
                                isSubmitting = false
                                if (success) {
                                    if (isLoginTab) {
                                        onAuthSuccess()
                                    } else {
                                        infoMessage = "Registered successfully! You can login now."
                                        isLoginTab = true
                                        password = ""
                                    }
                                } else {
                                    errorMessage = msg
                                }
                            }
                        },
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(48.dp),
                        shape = RoundedCornerShape(8.dp),
                        colors = ButtonDefaults.buttonColors(
                            containerColor = if (isLoginTab) ColorBlue.copy(alpha = 0.2f) else ColorGreen.copy(alpha = 0.2f)
                        ),
                        border = BorderStroke(1.5.dp, if (isLoginTab) ColorBlue else ColorGreen),
                        enabled = !isSubmitting
                    ) {
                        if (isSubmitting) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(24.dp),
                                color = if (isLoginTab) ColorBlue else ColorGreen,
                                strokeWidth = 2.dp
                            )
                        } else {
                            Text(
                                text = if (isLoginTab) "▶ ACCESS ORCHESTRATOR" else "⚡ CREATE NEW ACCOUNT",
                                color = if (isLoginTab) ColorBlue else ColorGreen,
                                fontSize = 12.sp,
                                fontWeight = FontWeight.Bold,
                                fontFamily = FontFamily.Monospace
                            )
                        }
                    }
                }
            }
        }
    }
}

suspend fun performAuth(
    baseUrl: String,
    endpoint: String,
    username: String,
    password: String
): Pair<Boolean, String> = withContext(Dispatchers.IO) {
    try {
        val cleanBase = baseUrl.removeSuffix("/")
        val url = URL("$cleanBase$endpoint")
        val conn = url.openConnection() as HttpURLConnection
        conn.requestMethod = "POST"
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
        conn.doOutput = true
        conn.connectTimeout = 8000
        conn.readTimeout = 8000

        val payload = JSONObject().apply {
            put("username", username)
            put("password", password)
        }

        val writer = OutputStreamWriter(conn.outputStream)
        writer.write(payload.toString())
        writer.flush()
        writer.close()

        val responseCode = conn.responseCode
        val stream = if (responseCode == HttpURLConnection.HTTP_OK) conn.inputStream else conn.errorStream
        if (stream == null) {
            return@withContext Pair(false, "Server returned response code $responseCode")
        }
        val reader = BufferedReader(InputStreamReader(stream))
        val sb = StringBuilder()
        var line: String?
        while (reader.readLine().also { line = it } != null) {
            sb.append(line)
        }
        reader.close()
        conn.disconnect()

        val resp = JSONObject(sb.toString())
        val status = resp.optString("status", "error")
        val message = resp.optString("message", "Authentication failed")
        
        if (responseCode == HttpURLConnection.HTTP_OK && status == "success") {
            Pair(true, message)
        } else {
            Pair(false, message)
        }
    } catch (e: Exception) {
        Pair(false, e.message ?: "Network error occurred")
    }
}

// =============================================================================
// 3D HEXAGON LOGO COMPOSABLE
// =============================================================================

@Composable
fun Logo3D(modifier: Modifier = Modifier) {
    Canvas(modifier = modifier) {
        val w = size.width
        val h = size.height
        val R = minOf(w, h) / 2f
        val cx = w / 2f
        val cy = h / 2f

        // 1. Drop Shadow / Ambient Glow (soft radial gradient)
        val glowPath = Path().apply {
            for (i in 0..5) {
                val angleRad = Math.toRadians((i * 60 - 90).toDouble())
                val x = cx + (R * 0.95f) * Math.cos(angleRad).toFloat()
                val y = cy + (R * 0.95f) * Math.sin(angleRad).toFloat()
                if (i == 0) moveTo(x, y) else lineTo(x, y)
            }
            close()
        }
        drawPath(
            path = glowPath,
            brush = Brush.radialGradient(
                colors = listOf(ColorBlue.copy(alpha = 0.4f), Color.Transparent),
                center = Offset(cx, cy),
                radius = R * 1.2f
            )
        )

        // 2. 3D Extrusion Layer (Bevel/Depth)
        val depthOffset = R * 0.12f
        val depthPath = Path().apply {
            for (i in 0..5) {
                val angleRad = Math.toRadians((i * 60 - 90).toDouble())
                val x = cx + depthOffset + (R * 0.75f) * Math.cos(angleRad).toFloat()
                val y = cy + depthOffset + (R * 0.75f) * Math.sin(angleRad).toFloat()
                if (i == 0) moveTo(x, y) else lineTo(x, y)
            }
            close()
        }
        drawPath(
            path = depthPath,
            brush = Brush.linearGradient(
                colors = listOf(Color(0xFF004488), Color(0xFF440088)),
                start = Offset(0f, 0f),
                end = Offset(w, h)
            )
        )

        // 3. Connecting sides for solid 3D extrusion look
        val frontR = R * 0.75f
        val frontCx = cx
        val frontCy = cy
        val backCx = cx + depthOffset
        val backCy = cy + depthOffset

        for (i in 0..5) {
            val a1 = Math.toRadians((i * 60 - 90).toDouble())
            val a2 = Math.toRadians(((i + 1) * 60 - 90).toDouble())
            val p1Front = Offset(frontCx + frontR * Math.cos(a1).toFloat(), frontCy + frontR * Math.sin(a1).toFloat())
            val p2Front = Offset(frontCx + frontR * Math.cos(a2).toFloat(), frontCy + frontR * Math.sin(a2).toFloat())
            val p1Back = Offset(backCx + frontR * Math.cos(a1).toFloat(), backCy + frontR * Math.sin(a1).toFloat())
            val p2Back = Offset(backCx + frontR * Math.cos(a2).toFloat(), backCy + frontR * Math.sin(a2).toFloat())

            val sidePath = Path().apply {
                moveTo(p1Front.x, p1Front.y)
                lineTo(p2Front.x, p2Front.y)
                lineTo(p2Back.x, p2Back.y)
                lineTo(p1Back.x, p1Back.y)
                close()
            }
            val sideColor = if (i in 1..3) Color(0xFF0A1128) else Color(0xFF1E295D)
            drawPath(path = sidePath, color = sideColor)
        }

        // 4. Front Face Hexagon
        val frontPath = Path().apply {
            for (i in 0..5) {
                val angleRad = Math.toRadians((i * 60 - 90).toDouble())
                val x = frontCx + frontR * Math.cos(angleRad).toFloat()
                val y = frontCy + frontR * Math.sin(angleRad).toFloat()
                if (i == 0) moveTo(x, y) else lineTo(x, y)
            }
            close()
        }
        drawPath(
            path = frontPath,
            brush = Brush.linearGradient(
                colors = listOf(ColorBlue, ColorAccent),
                start = Offset(0f, 0f),
                end = Offset(w, h)
            )
        )

        // 5. Inner Hexagon Cutout (to make it hollow/ring-like)
        val innerR = frontR * 0.6f
        val innerPath = Path().apply {
            for (i in 0..5) {
                val angleRad = Math.toRadians((i * 60 - 90).toDouble())
                val x = frontCx + innerR * Math.cos(angleRad).toFloat()
                val y = frontCy + innerR * Math.sin(angleRad).toFloat()
                if (i == 0) moveTo(x, y) else lineTo(x, y)
            }
            close()
        }
        drawPath(
            path = innerPath,
            color = ColorBG
        )

        // 6. Highlight Rim on Front Face (adds a gloss/reflection effect)
        drawPath(
            path = frontPath,
            brush = Brush.linearGradient(
                colors = listOf(Color.White.copy(alpha = 0.6f), Color.Transparent),
                start = Offset(0f, 0f),
                end = Offset(w * 0.8f, h * 0.8f)
            ),
            style = Stroke(width = R * 0.05f)
        )

        // 7. Inner Hexagon Rim Glow
        drawPath(
            path = innerPath,
            brush = Brush.linearGradient(
                colors = listOf(ColorGreen, ColorBlue),
                start = Offset(0f, 0f),
                end = Offset(w, h)
            ),
            style = Stroke(width = R * 0.04f)
        )
    }
}
