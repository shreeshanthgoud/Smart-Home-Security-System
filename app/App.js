import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert, FlatList, Image, Modal, Pressable, RefreshControl, SafeAreaView,
  ScrollView, StatusBar, StyleSheet, Text, TextInput, Vibration, View,
} from "react-native";

// Set EXPO_PUBLIC_API_URL in app/.env to the backend's LAN address.
const API = (process.env.EXPO_PUBLIC_API_URL || "").replace(/\/$/, "");
const POLL_MS = 3000;

function requireApi() {
  if (!API) throw new Error("Set EXPO_PUBLIC_API_URL in app/.env to connect to the backend.");
}

const C = {
  navy: "#1B2A4A", blue: "#2B7FE0", red: "#E4502A", amber: "#F2A33A",
  green: "#2E9E5B", bg: "#F3F6FA", card: "#FFFFFF", muted: "#5A6B8C", line: "#E1E7F0",
};
const TYPES = {
  unknown_visitor: { label: "Unknown visitor", color: C.amber },
  theft: { label: "Theft alert", color: C.red },
  abnormal_activity: { label: "Abnormal activity", color: C.red },
};
const FILTERS = [
  ["all", "All"], ["unknown_visitor", "Visitors"], ["theft", "Theft"], ["abnormal_activity", "Activity"],
];

// ---------- API ----------
async function getEvents() {
  requireApi();
  const r = await fetch(`${API}/events?limit=50`);
  if (!r.ok) throw new Error(`Server returned ${r.status}`);
  return r.json();
}
async function sendDecision(id, decision) {
  requireApi();
  const r = await fetch(`${API}/events/${id}/decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision }),
  });
  if (!r.ok) throw new Error(`Server returned ${r.status}`);
}
async function sendChat(message) {
  requireApi();
  const r = await fetch(`${API}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `Server returned ${r.status}`);
  return data;
}

// ---------- helpers ----------
const imgUrl = (e) => (e.snapshot_path ? `${API}${e.snapshot_path}` : null);
function ago(ts) {
  const s = Math.max(0, (Date.now() - new Date(ts).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(ts).toLocaleDateString();
}
const dayKey = (ts) => new Date(ts).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "short" });
function badge(e) {
  if (e.status === "pending") return { text: "Needs action", color: C.amber };
  if (e.decision === "accepted") return { text: "Accepted", color: C.green };
  if (e.decision === "rejected") return { text: "Rejected", color: C.red };
  return { text: "Resolved", color: C.muted };
}

// ---------- components ----------
function Snapshot({ event, style }) {
  const [failed, setFailed] = useState(false);
  const uri = imgUrl(event);
  if (!uri || failed) return <View style={[styles.ph, style]}><Text style={styles.phText}>No image</Text></View>;
  return <Image source={{ uri }} style={style} resizeMode="cover" onError={() => setFailed(true)} />;
}

function EventCard({ event, onPress }) {
  const t = TYPES[event.type] || { label: event.type, color: C.muted };
  const b = badge(event);
  return (
    <Pressable style={styles.card} onPress={() => onPress(event)}>
      <Snapshot event={event} style={styles.thumb} />
      <View style={styles.cardBody}>
        <Text style={styles.cardTitle}>{t.label}</Text>
        <Text style={styles.muted}>{event.camera} · {ago(event.timestamp)}</Text>
        <View style={[styles.pill, { backgroundColor: b.color + "22" }]}>
          <Text style={[styles.pillText, { color: b.color }]}>{b.text}</Text>
        </View>
      </View>
    </Pressable>
  );
}

function DecisionButtons({ event, onDone }) {
  const [busy, setBusy] = useState(false);
  const act = (decision) =>
    Alert.alert(
      decision === "accepted" ? "Let this visitor in?" : "Keep the door locked?",
      "This is sent to your door right away.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: decision === "accepted" ? "Accept" : "Reject",
          onPress: async () => {
            setBusy(true);
            try { await sendDecision(event.id, decision); onDone(); }
            catch (err) { Alert.alert("Could not send", err.message); }
            setBusy(false);
          },
        },
      ]
    );
  return (
    <View style={styles.row}>
      <Pressable disabled={busy} style={[styles.btn, { backgroundColor: C.red }]} onPress={() => act("rejected")}>
        <Text style={styles.btnText}>Reject</Text>
      </Pressable>
      <Pressable disabled={busy} style={[styles.btn, { backgroundColor: C.green }]} onPress={() => act("accepted")}>
        <Text style={styles.btnText}>Accept</Text>
      </Pressable>
    </View>
  );
}

function EventSheet({ event, onClose, onDone, urgent }) {
  if (!event) return null;
  const t = TYPES[event.type] || { label: event.type };
  const canDecide = event.type === "unknown_visitor" && event.status === "pending";
  return (
    <Modal visible animationType="slide" onRequestClose={onClose}>
      <SafeAreaView style={styles.sheet}>
        <ScrollView contentContainerStyle={{ padding: 20 }}>
          <Text style={[styles.h1, urgent && { color: C.red }]}>
            {urgent ? `Unknown visitor at ${event.camera}` : t.label}
          </Text>
          <Snapshot event={event} style={styles.big} />
          <Text style={styles.detail}>Camera: {event.camera}</Text>
          <Text style={styles.detail}>Time: {new Date(event.timestamp).toLocaleString()}</Text>
          <Text style={styles.detail}>Status: {badge(event).text}</Text>
          {canDecide ? <DecisionButtons event={event} onDone={onDone} /> : null}
          <Pressable style={styles.close} onPress={onClose}>
            <Text style={styles.closeText}>{canDecide ? "Decide later" : "Close"}</Text>
          </Pressable>
        </ScrollView>
      </SafeAreaView>
    </Modal>
  );
}

function ChatScreen() {
  const [messages, setMessages] = useState([
    { id: "welcome", role: "assistant", text: "Hi! Ask me about recent alerts or your monitoring status." },
  ]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    const message = draft.trim();
    if (!message || busy) return;
    setDraft("");
    setMessages((items) => [...items, { id: `${Date.now()}-user`, role: "user", text: message }]);
    setBusy(true);
    try {
      const result = await sendChat(message);
      setMessages((items) => [...items, { id: `${Date.now()}-assistant`, role: "assistant", text: result.reply, image: result.image }]);
    } catch (err) {
      setMessages((items) => [...items, { id: `${Date.now()}-error`, role: "assistant", text: `I couldn't get a reply: ${err.message}` }]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <SafeAreaView style={styles.chatScreen}>
      <Text style={styles.h1}>Home Assistant</Text>
      <FlatList
        style={styles.chatList}
        contentContainerStyle={styles.chatMessages}
        data={messages}
        keyExtractor={(item) => item.id}
        renderItem={({ item }) => (
          <View style={[styles.chatBubble, item.role === "user" ? styles.userBubble : styles.assistantBubble]}>
            {item.image ? <Image source={{ uri: item.image }} style={styles.chatImage} resizeMode="cover" /> : null}
            <Text style={[styles.chatText, item.role === "user" && styles.userChatText]}>{item.text}</Text>
          </View>
        )}
        ListFooterComponent={busy ? <Text style={styles.chatTyping}>Checking your monitoring status…</Text> : null}
      />
      <View style={styles.chatComposer}>
        <TextInput
          style={styles.chatInput}
          value={draft}
          onChangeText={setDraft}
          placeholder="Ask about your home status"
          placeholderTextColor={C.muted}
          editable={!busy}
          onSubmitEditing={submit}
          returnKeyType="send"
        />
        <Pressable style={[styles.chatSend, busy && { opacity: 0.55 }]} disabled={busy || !draft.trim()} onPress={submit}>
          <Text style={styles.chatSendText}>Send</Text>
        </Pressable>
      </View>
    </SafeAreaView>
  );
}

// ---------- main ----------
export default function App() {
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [tab, setTab] = useState("alerts");
  const [filter, setFilter] = useState("all");
  const [selected, setSelected] = useState(null);
  const [urgent, setUrgent] = useState(null);
  const seen = useRef(null); // ids already known, so only NEW visitors pop up

  const load = useCallback(async () => {
    try {
      const data = await getEvents();
      setEvents(data);
      setError(null);
      if (seen.current === null) {
        seen.current = new Set(data.map((e) => e.id));
      } else {
        const fresh = data.find(
          (e) => !seen.current.has(e.id) && e.type === "unknown_visitor" && e.status === "pending"
        );
        data.forEach((e) => seen.current.add(e.id));
        if (fresh) { Vibration.vibrate([0, 400, 200, 400]); setUrgent(fresh); }
      }
      setSelected((s) => (s ? data.find((e) => e.id === s.id) || s : s));
      setUrgent((u) => (u ? data.find((e) => e.id === u.id) || u : u));
    } catch (err) {
      setError(err.message);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  const pending = events.filter((e) => e.status === "pending");
  const alerts = events.filter((e) => filter === "all" || e.type === filter);
  const history = events.filter((e) => e.status === "resolved");
  const groups = history.reduce((acc, e) => {
    const k = dayKey(e.timestamp);
    (acc[k] = acc[k] || []).push(e);
    return acc;
  }, {});

  const Header = (
    <View>
      <Text style={styles.h1}>Smart Home Monitor</Text>
      <View style={[styles.status, { backgroundColor: pending.length ? C.red : C.green }]}>
        <Text style={styles.statusText}>
          {pending.length ? `${pending.length} alert${pending.length > 1 ? "s" : ""} need your attention` : "All clear"}
        </Text>
      </View>
      {error ? (
        <Pressable style={styles.error} onPress={load}>
          <Text style={styles.errorText}>Can't reach the backend ({error}). Tap to retry.</Text>
        </Pressable>
      ) : null}
    </View>
  );

  return (
    <SafeAreaView style={styles.root}>
      <StatusBar barStyle="dark-content" />
      {tab === "chat" ? <ChatScreen /> : tab === "alerts" ? (
        <FlatList
          contentContainerStyle={{ padding: 16 }}
          data={alerts}
          keyExtractor={(e) => e.id}
          refreshControl={<RefreshControl refreshing={false} onRefresh={load} />}
          ListHeaderComponent={
            <View>
              {Header}
              <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginVertical: 12 }}>
                {FILTERS.map(([k, l]) => (
                  <Pressable key={k} onPress={() => setFilter(k)}
                    style={[styles.chip, filter === k && { backgroundColor: C.navy }]}>
                    <Text style={[styles.chipText, filter === k && { color: "#fff" }]}>{l}</Text>
                  </Pressable>
                ))}
              </ScrollView>
            </View>
          }
          ListEmptyComponent={<Text style={styles.empty}>{loading ? "Loading…" : "No events yet. Alerts from your cameras will appear here."}</Text>}
          renderItem={({ item }) => <EventCard event={item} onPress={setSelected} />}
        />
      ) : (
        <ScrollView contentContainerStyle={{ padding: 16 }}>
          <Text style={styles.h1}>History</Text>
          {error ? <Text style={styles.errorText}>{error}</Text> : null}
          {Object.keys(groups).length === 0 ? <Text style={styles.empty}>No resolved events yet.</Text> : null}
          {Object.entries(groups).map(([day, list]) => (
            <View key={day}>
              <Text style={styles.day}>{day}</Text>
              {list.map((e) => <EventCard key={e.id} event={e} onPress={setSelected} />)}
            </View>
          ))}
        </ScrollView>
      )}

      <View style={styles.tabs}>
        {[["alerts", "Alerts"], ["history", "History"], ["chat", "Chat"]].map(([k, l]) => (
          <Pressable key={k} style={styles.tab} onPress={() => setTab(k)}>
            <Text style={[styles.tabText, tab === k && { color: C.blue }]}>
              {l}{k === "alerts" && pending.length ? ` (${pending.length})` : ""}
            </Text>
          </Pressable>
        ))}
      </View>

      <EventSheet event={urgent} urgent onClose={() => setUrgent(null)} onDone={() => { setUrgent(null); load(); }} />
      <EventSheet event={selected} onClose={() => setSelected(null)} onDone={() => { setSelected(null); load(); }} />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: C.bg },
  h1: { fontSize: 24, fontWeight: "700", color: C.navy, marginBottom: 12 },
  status: { borderRadius: 14, padding: 16 },
  statusText: { color: "#fff", fontSize: 16, fontWeight: "600" },
  error: { backgroundColor: "#FDECE7", borderRadius: 12, padding: 12, marginTop: 12 },
  errorText: { color: C.red, fontSize: 14 },
  chip: { paddingHorizontal: 14, paddingVertical: 8, borderRadius: 20, backgroundColor: C.card, marginRight: 8, borderWidth: 1, borderColor: C.line },
  chipText: { color: C.navy, fontWeight: "600" },
  card: { flexDirection: "row", backgroundColor: C.card, borderRadius: 14, padding: 10, marginBottom: 10, borderWidth: 1, borderColor: C.line },
  thumb: { width: 84, height: 84, borderRadius: 10 },
  ph: { backgroundColor: C.line, alignItems: "center", justifyContent: "center" },
  phText: { color: C.muted, fontSize: 12 },
  cardBody: { flex: 1, marginLeft: 12, justifyContent: "center" },
  cardTitle: { fontSize: 16, fontWeight: "700", color: C.navy, marginBottom: 2 },
  muted: { color: C.muted, fontSize: 13 },
  pill: { alignSelf: "flex-start", borderRadius: 10, paddingHorizontal: 10, paddingVertical: 3, marginTop: 8 },
  pillText: { fontSize: 12, fontWeight: "700" },
  day: { fontSize: 14, fontWeight: "700", color: C.muted, marginVertical: 10 },
  empty: { textAlign: "center", color: C.muted, marginTop: 40, paddingHorizontal: 20 },
  tabs: { flexDirection: "row", borderTopWidth: 1, borderTopColor: C.line, backgroundColor: C.card },
  tab: { flex: 1, alignItems: "center", paddingVertical: 16 },
  tabText: { fontSize: 15, fontWeight: "700", color: C.muted },
  sheet: { flex: 1, backgroundColor: C.bg },
  big: { width: "100%", height: 280, borderRadius: 14, marginBottom: 16 },
  detail: { fontSize: 15, color: C.navy, marginBottom: 6 },
  row: { flexDirection: "row", gap: 12, marginTop: 20 },
  btn: { flex: 1, borderRadius: 14, paddingVertical: 18, alignItems: "center" },
  btnText: { color: "#fff", fontSize: 18, fontWeight: "700" },
  close: { alignItems: "center", paddingVertical: 18 },
  closeText: { color: C.blue, fontSize: 16, fontWeight: "600" },
  chatScreen: { flex: 1, backgroundColor: C.bg, padding: 16 },
  chatList: { flex: 1 },
  chatMessages: { paddingVertical: 8, gap: 10 },
  chatBubble: { maxWidth: "86%", borderRadius: 16, paddingHorizontal: 14, paddingVertical: 11 },
  userBubble: { alignSelf: "flex-end", backgroundColor: C.blue },
  assistantBubble: { alignSelf: "flex-start", backgroundColor: C.card, borderWidth: 1, borderColor: C.line },
  chatText: { color: C.navy, fontSize: 15, lineHeight: 21 },
  chatImage: { width: 250, height: 180, borderRadius: 10, marginBottom: 8 },
  userChatText: { color: "#fff" },
  chatTyping: { color: C.muted, paddingVertical: 8 },
  chatComposer: { flexDirection: "row", alignItems: "center", gap: 8, paddingTop: 10 },
  chatInput: { flex: 1, minHeight: 48, borderRadius: 14, backgroundColor: C.card, borderWidth: 1, borderColor: C.line, paddingHorizontal: 14, color: C.navy },
  chatSend: { minHeight: 48, borderRadius: 14, backgroundColor: C.blue, justifyContent: "center", paddingHorizontal: 16 },
  chatSendText: { color: "#fff", fontSize: 15, fontWeight: "700" },
});
