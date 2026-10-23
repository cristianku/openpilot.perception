package ch.bodhi.perception

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.Check
import androidx.compose.material.icons.outlined.Remove
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** The palette of perception-web (web/static/app.css, dark): navy with blue accents. */
object Site {
    val Background = Color(0xFF0B0F16)
    val Surface = Color(0xFF161B24)
    val Surface2 = Color(0xFF1C2230)
    val Surface3 = Color(0xFF232B3B)
    val Text = Color(0xFFE8ECF3)
    val Muted = Color(0xFF9AA4B2)
    val Accent = Color(0xFF3B82F6)
    val AccentSoft = Color(0x293B82F6)
    val Ok = Color(0xFF4ADE80)
    val OkSoft = Color(0x214ADE80)
    val Todo = Color(0xFFFBBF24)
    val TodoSoft = Color(0x21FBBF24)
    val Bad = Color(0xFFF87171)
    val BadSoft = Color(0x24F87171)
    val Info = Color(0xFF60A5FA)
    val Line = Color(0x24FFFFFF)
    val Series = Color(0xFF4F8FF7)
}

private val SiteColors = darkColorScheme(
    primary = Site.Accent, onPrimary = Color.White,
    background = Site.Background, onBackground = Site.Text,
    surface = Site.Surface, onSurface = Site.Text,
    surfaceVariant = Site.Surface2, onSurfaceVariant = Site.Muted,
    secondaryContainer = Site.AccentSoft, onSecondaryContainer = Site.Text,
    error = Site.Bad, outline = Site.Line
)

@Composable
fun PerceptionTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = SiteColors, content = content)
}

/** How a line of a checklist or a callout reads: done, still to do, wrong, or just a note. */
enum class Tone(val color: Color, val soft: Color) {
    OK(Site.Ok, Site.OkSoft), TODO(Site.Todo, Site.TodoSoft), BAD(Site.Bad, Site.BadSoft),
    INFO(Site.Info, Site.AccentSoft), MUTED(Site.Muted, Site.Surface2)
}

/** A wizard step as on the site: back arrow and dots on top, the big title, one decision, the pinned button. */
@Composable
fun StepScreen(title: String, subtitle: String = "", step: Int = 0, count: Int = 0, onBack: (() -> Unit)? = null,
               bottom: (@Composable ColumnScope.() -> Unit)? = null, content: @Composable ColumnScope.() -> Unit) {
    Column(Modifier.fillMaxSize().background(Site.Background)) {
        Column(
            modifier = Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState())
                .padding(horizontal = 20.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp)
        ) {
            Box(Modifier.fillMaxWidth().height(44.dp)) {
                if (onBack != null) {
                    IconButton(onClick = onBack, modifier = Modifier.align(Alignment.CenterStart)) {
                        Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "Back")
                    }
                }
                if (count > 1) {
                    Row(Modifier.align(Alignment.Center), horizontalArrangement = Arrangement.spacedBy(7.dp)) {
                        for (index in 1..count) {
                            Box(Modifier.size(7.dp).clip(CircleShape)
                                .background(if (index == step) Site.Accent else Site.Surface3))
                        }
                    }
                }
            }
            Text(title, fontSize = 28.sp, lineHeight = 33.sp, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center,
                modifier = Modifier.fillMaxWidth())
            if (subtitle.isNotBlank()) {
                Text(subtitle, color = Site.Muted, textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth())
            }
            content()
            if (count > 1) {
                Text("Step $step of $count", style = MaterialTheme.typography.bodySmall, color = Site.Muted,
                    textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth())
            }
            Spacer(Modifier.height(4.dp))
        }
        if (bottom != null) {
            Column(Modifier.fillMaxWidth().background(Site.Surface).padding(horizontal = 20.dp, vertical = 12.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp), content = bottom)
        }
    }
}

/** The one big button of a step; under it why it cannot go on yet. */
@Composable
fun PrimaryButton(label: String, reason: String = "", busy: Boolean = false, danger: Boolean = false, onClick: () -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Button(
            onClick = onClick, enabled = reason.isEmpty() && !busy, shape = RoundedCornerShape(14.dp),
            colors = ButtonDefaults.buttonColors(containerColor = if (danger) Color(0xFFDC2626) else Site.Accent),
            modifier = Modifier.fillMaxWidth().height(54.dp)
        ) {
            if (busy) CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp, color = Color.White)
            else Text(label, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
        }
        if (reason.isNotEmpty()) {
            Text(reason, style = MaterialTheme.typography.bodySmall, color = Site.Muted, textAlign = TextAlign.Center,
                modifier = Modifier.fillMaxWidth())
        }
    }
}

@Composable
fun SecondaryButton(label: String, enabled: Boolean = true, busy: Boolean = false, onClick: () -> Unit) {
    OutlinedButton(onClick = onClick, enabled = enabled && !busy, shape = RoundedCornerShape(14.dp),
        border = BorderStroke(1.dp, Site.Line), modifier = Modifier.fillMaxWidth().height(50.dp)) {
        if (busy) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
        else Text(label, fontSize = 15.sp, color = Site.Text)
    }
}

@Composable
fun LinkButton(label: String, color: Color = Site.Info, onClick: () -> Unit) {
    TextButton(onClick = onClick, modifier = Modifier.fillMaxWidth()) { Text(label, color = color) }
}

/** A card of the site: rounded, the surface colour, a small capital title. */
@Composable
fun SectionCard(modifier: Modifier = Modifier, title: String = "", content: @Composable ColumnScope.() -> Unit) {
    Card(shape = RoundedCornerShape(18.dp), modifier = modifier.fillMaxWidth(), border = BorderStroke(1.dp, Site.Line),
        colors = CardDefaults.cardColors(containerColor = Site.Surface)) {
        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            if (title.isNotEmpty()) {
                Text(title.uppercase(), style = MaterialTheme.typography.labelLarge, fontWeight = FontWeight.SemiBold,
                    color = Site.Muted)
            }
            content()
        }
    }
}

/** A coloured box with one message, as the callouts of the site. */
@Composable
fun Callout(text: String, tone: Tone = Tone.INFO, action: (@Composable () -> Unit)? = null) {
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(tone.soft).padding(14.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(text, color = if (tone == Tone.MUTED) Site.Text else tone.color)
        action?.invoke()
    }
}

/** A line of a checklist: a coloured mark and the text. */
@Composable
fun CheckLine(text: String, tone: Tone = Tone.OK) {
    Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.Top) {
        Box(Modifier.padding(top = 3.dp).size(18.dp).clip(CircleShape).background(tone.soft), contentAlignment = Alignment.Center) {
            Box(Modifier.size(7.dp).clip(CircleShape).background(tone.color))
        }
        Text(text, modifier = Modifier.weight(1f), color = if (tone == Tone.MUTED) Site.Muted else Site.Text)
    }
}

@Composable
fun Note(text: String, color: Color = Site.Muted, center: Boolean = true) {
    Text(text, style = MaterialTheme.typography.bodySmall, color = color,
        textAlign = if (center) TextAlign.Center else TextAlign.Start, modifier = Modifier.fillMaxWidth())
}

/** A small coloured label: "On the phone", "On the site". */
@Composable
fun Pill(text: String, tone: Tone) {
    Text(text, color = tone.color, fontSize = 12.sp, fontWeight = FontWeight.SemiBold,
        modifier = Modifier.clip(RoundedCornerShape(50)).background(tone.soft).padding(horizontal = 10.dp, vertical = 4.dp))
}

/** Monospace text that wraps, for keys and fingerprints. */
@Composable
fun Mono(text: String, color: Color = Site.Text) {
    Text(text, fontFamily = FontFamily.Monospace, fontSize = 12.5.sp, color = color, lineHeight = 17.sp,
        modifier = Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).background(Site.Surface2).padding(10.dp))
}

/** A big choice card: an icon, the title, one line, the check mark when chosen; an optional tag ("Needed"). */
@Composable
fun ChoiceCard(title: String, detail: String, icon: ImageVector, selected: Boolean, tag: String? = null, onClick: () -> Unit) {
    Card(
        shape = RoundedCornerShape(18.dp),
        border = BorderStroke(if (selected) 2.dp else 1.dp, if (selected) Site.Accent else Site.Line),
        colors = CardDefaults.cardColors(containerColor = Site.Surface2),
        modifier = Modifier.fillMaxWidth().clickable(onClick = onClick)
    ) {
        Row(
            Modifier.background(Brush.radialGradient(listOf(Site.AccentSoft, Color.Transparent))).padding(16.dp),
            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)
        ) {
            Box(Modifier.size(52.dp).clip(RoundedCornerShape(14.dp)).background(Site.AccentSoft), contentAlignment = Alignment.Center) {
                Icon(icon, contentDescription = null, tint = Color(0xFF93C5FD), modifier = Modifier.size(28.dp))
            }
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(title, fontSize = 18.sp, fontWeight = FontWeight.Bold)
                    if (tag != null) Pill(tag, Tone.TODO)
                }
                if (detail.isNotBlank()) Text(detail, style = MaterialTheme.typography.bodyMedium, color = Site.Muted)
            }
            Box(Modifier.size(28.dp).clip(CircleShape).background(if (selected) Site.Accent else Color.Transparent),
                contentAlignment = Alignment.Center) {
                if (selected) Icon(Icons.Outlined.Check, contentDescription = "Chosen", tint = Color.White, modifier = Modifier.size(18.dp))
            }
        }
    }
}

/** A distance in metres: − and + by 1 cm (and 10 cm), or typed ("1,32" works as "1.32"). */
@Composable
fun MetreStepper(value: Double?, onChange: (Double?) -> Unit, label: String, start: Double) {
    var text by remember { mutableStateOf(value?.let(RadarMath::format2) ?: "") }
    var typedProblem by remember { mutableStateOf(false) }
    fun set(next: Double) {
        val clamped = RadarMath.round2(next.coerceIn(RadarMath.TAPE_MIN_M, RadarMath.TAPE_MAX_M))
        text = RadarMath.format2(clamped)
        typedProblem = false
        onChange(clamped)
    }
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(label, color = Site.Muted, textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth())
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp),
            modifier = Modifier.fillMaxWidth()) {
            StepButton(Icons.Outlined.Remove, "1 cm less") { set((value ?: start) - 0.01) }
            OutlinedTextField(
                value = text,
                onValueChange = { typed ->
                    text = typed
                    val parsed = RadarMath.parseMetres(typed)
                    typedProblem = typed.isNotBlank() && parsed == null
                    onChange(parsed)
                },
                singleLine = true, shape = RoundedCornerShape(14.dp),
                textStyle = MaterialTheme.typography.headlineMedium.copy(textAlign = TextAlign.Center, fontWeight = FontWeight.Bold),
                suffix = { Text("m", color = Site.Muted) },
                isError = typedProblem,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                modifier = Modifier.weight(1f)
            )
            StepButton(Icons.Outlined.Add, "1 cm more") { set((value ?: start) + 0.01) }
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Box(Modifier.weight(1f)) { SecondaryButton("−10 cm") { set((value ?: start) - 0.10) } }
            Box(Modifier.weight(1f)) { SecondaryButton("+10 cm") { set((value ?: start) + 0.10) } }
        }
        if (typedProblem) Note("A distance in metres, for example 1.32 or 1,32.", Site.Bad)
    }
}

@Composable
private fun StepButton(icon: ImageVector, description: String, onClick: () -> Unit) {
    Box(Modifier.size(56.dp).clip(RoundedCornerShape(12.dp)).background(Site.Surface3).clickable(onClick = onClick),
        contentAlignment = Alignment.Center) {
        Icon(icon, contentDescription = description, tint = Site.Text)
    }
}

@Composable
fun Confirm(title: String, text: String, confirm: String, danger: Boolean = true, onConfirm: () -> Unit, onDismiss: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = { Text(text) },
        confirmButton = { TextButton(onClick = onConfirm) { Text(confirm, color = if (danger) Site.Bad else Site.Info) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
        containerColor = Site.Surface
    )
}

/** 23 Oct, 09:29 */
fun shortDate(unix: Double?): String {
    if (unix == null || unix <= 0) return ""
    val format = java.text.SimpleDateFormat("d MMM, HH:mm", java.util.Locale.ENGLISH)
    return format.format(java.util.Date((unix * 1000).toLong()))
}

/** "12 s", "3 min" */
fun shortSpan(seconds: Double): String = when {
    seconds < 60 -> "${seconds.toInt()} s"
    seconds < 3600 -> "${(seconds / 60).toInt()} min"
    else -> "${(seconds / 3600).toInt()} h"
}
