
"use strict";

/* APPLICATION STATE */

const state = {
    authenticated: false,
    loading: false,
    scanLoading: false,
    interfaceLoading: false,
    pollingTimer: null,
    smsPollingTimer: null,
    zerotierPollingTimer: null,
    smsLoading: false,
    sessionCheckInProgress: false
};

const hotspotClientSamples = new Map();

const API = "/api/v1";


/* ADMIN NOTIFICATIONS */
const notificationKeys={items:"keyland.notify.items.v1",sms:"keyland.notify.sms.v1",clients:"keyland.notify.clients.v1",hotspot:"keyland.notify.hotspot.v1",internet:"keyland.notify.internet.v1",cellular:"keyland.notify.cellular.v1",resources:"keyland.notify.resources.v1"};
function sessionRead(key,fallback){try{const raw=sessionStorage.getItem(key);return raw===null?fallback:JSON.parse(raw)}catch(_){return fallback}}
const savedItems=sessionRead(notificationKeys.items,[]);
const savedSms=sessionRead(notificationKeys.sms,[]);
const notificationState={
 items:Array.isArray(savedItems)?savedItems.slice(0,100):[],
 seenSms:new Set(Array.isArray(savedSms)?savedSms.map(String):[]),
 smsReady:sessionStorage.getItem(notificationKeys.sms)!==null,
 clients:sessionRead(notificationKeys.clients,null),
 hotspot:sessionRead(notificationKeys.hotspot,null),
 internet:sessionRead(notificationKeys.internet,null),
 cellular:sessionRead(notificationKeys.cellular,null),
 resources:sessionRead(notificationKeys.resources,"")
};
const notificationApiErrors={};let notificationSeq=0;
function saveNotifications(){try{
 sessionStorage.setItem(notificationKeys.items,JSON.stringify(notificationState.items.slice(0,100)));
 sessionStorage.setItem(notificationKeys.sms,JSON.stringify(Array.from(notificationState.seenSms).slice(-500)));
 for(const key of ["clients","hotspot","internet","cellular","resources"])if(notificationState[key]!==null)sessionStorage.setItem(notificationKeys[key],JSON.stringify(notificationState[key]));
}catch(_){}}
function addNotification(type,titleKa,titleEn,bodyKa,bodyEn){
 if(!state.authenticated)return;
 notificationState.items.unshift({id:Date.now()+"-"+(++notificationSeq),type,titleKa,titleEn,bodyKa:String(bodyKa||""),bodyEn:String(bodyEn||""),timestamp:new Date().toISOString(),read:false});
 notificationState.items=notificationState.items.slice(0,100);saveNotifications();renderNotifications();
}
function renderNotifications(){
 const list=element("notificationList"),badge=element("notificationBadge");if(!list||!badge)return;
 const unread=notificationState.items.filter(x=>!x.read).length;badge.textContent=unread>99?"99+":String(unread);badge.hidden=!unread;list.replaceChildren();
 if(!notificationState.items.length){const empty=document.createElement("div");empty.className="notification-empty";empty.textContent=tr("შეტყობინებები ჯერ არ არის.","No notifications yet.");list.append(empty);return}
 notificationState.items.forEach(item=>{const row=document.createElement("button");row.type="button";row.className="notification-item"+(item.read?"":" notification-unread")+(item.type==="warning"?" notification-warning":"")+(item.type==="error"?" notification-error":"");
 const title=document.createElement("strong"),body=document.createElement("span"),time=document.createElement("small");
 title.textContent=uiLanguage==="en"?item.titleEn:item.titleKa;body.textContent=uiLanguage==="en"?item.bodyEn:item.bodyKa;
 const stamp=new Date(item.timestamp);time.textContent=Number.isNaN(stamp.getTime())?"":stamp.toLocaleString(uiLanguage==="en"?"en-US":"ka-GE");
 row.append(title,body,time);row.addEventListener("click",()=>{item.read=true;saveNotifications();renderNotifications()});list.append(row)});
}
function setNotificationPanel(open){
 const panel=element("notificationPanel"),button=element("notificationToggle");
 if(!panel||!button)return;
 if(open&&panel.parentElement!==document.body)document.body.appendChild(panel);
 panel.classList.toggle("hidden",!open);
 button.setAttribute("aria-expanded",String(open));
}
element("notificationToggle")?.addEventListener("click",()=>setNotificationPanel(element("notificationPanel")?.classList.contains("hidden")));
element("notificationReadAll")?.addEventListener("click",()=>{notificationState.items.forEach(x=>x.read=true);saveNotifications();renderNotifications()});
element("notificationClear")?.addEventListener("click",()=>{notificationState.items.length=0;saveNotifications();renderNotifications()});
document.addEventListener("click",event=>{const wrap=document.querySelector(".notification-wrap");if(wrap&&!wrap.contains(event.target))setNotificationPanel(false)});
document.addEventListener("keydown",event=>{if(event.key==="Escape")setNotificationPanel(false)});
function trackHotspotNotifications(data){
 if(!data||typeof data.active!=="boolean")return;
 const active=data.active,current={};
 (active&&Array.isArray(data.clients)?data.clients:[]).forEach(client=>{const mac=String(client.mac||"").toLowerCase();if(mac)current[mac]={name:String(client.hostname||client.ip||mac),ip:String(client.ip||"")}});
 if(notificationState.clients===null){notificationState.clients=current;notificationState.hotspot=active;saveNotifications();return}
 if(notificationState.hotspot!==null&&active!==notificationState.hotspot)addNotification(active?"success":"warning",active?"Hotspot ჩაირთო":"Hotspot გამოირთო",active?"Hotspot started":"Hotspot stopped",active?"Keyland Hotspot აქტიურია.":"Keyland Hotspot შეჩერდა.",active?"Keyland Hotspot is active.":"Keyland Hotspot has stopped.");
 Object.keys(current).forEach(mac=>{if(!notificationState.clients[mac]){const d=current[mac];addNotification("info","ახალი მოწყობილობა","New device connected",d.name+" დაუკავშირდა Hotspot-ს"+(d.ip?" · "+d.ip:"")+" · "+mac,d.name+" connected to the hotspot"+(d.ip?" · "+d.ip:"")+" · "+mac)}});
 Object.keys(notificationState.clients||{}).forEach(mac=>{if(!current[mac]){const d=notificationState.clients[mac];addNotification("warning","მოწყობილობა გაითიშა","Device disconnected",(d.name||mac)+" გაითიშა Hotspot-იდან · "+mac,(d.name||mac)+" disconnected from the hotspot · "+mac)}});
 notificationState.clients=current;notificationState.hotspot=active;saveNotifications();
}
function trackInternetNotification(data){
 if(typeof data?.connected!=="boolean")return;
 if(notificationState.internet===null)notificationState.internet=data.connected;
 else if(data.connected!==notificationState.internet){const up=data.connected;addNotification(up?"success":"warning",up?"ინტერნეტი აღდგა":"ინტერნეტი გაითიშა",up?"Internet restored":"Internet disconnected",up?"ინტერნეტთან კავშირი აღდგა.":"ინტერნეტთან კავშირი დაიკარგა.",up?"Internet connectivity restored.":"Internet connectivity was lost.");notificationState.internet=up}
 saveNotifications();
}
function trackCellularNotification(data){
 const modems=Array.isArray(data?.modems)?data.modems:[],m=modems.find(x=>x&&x.available)||modems[0];if(!m)return;
 const n=m.network||{},s=m.sim||{},o=m.operator||{},fp=JSON.stringify([m.available,m.state,s.detected,s.state,n.registration_state,n.technology,o.name]);
 if(notificationState.cellular===null)notificationState.cellular=fp;
 else if(fp!==notificationState.cellular){const details=[m.state,n.registration_state,n.technology,o.name].filter(Boolean).join(" · ");addNotification("info","SIM/LTE მდგომარეობა შეიცვალა","SIM/LTE status changed","მობილური ქსელის ახალი მდგომარეობა: "+details,"New mobile network state: "+details);notificationState.cellular=fp}
 saveNotifications();
}
function trackResourceNotifications(data){
 if(!data)return;const warnings=[],cpu=Number(data.cpu_percent),ram=Number(data.memory?.percent),disk=Number(data.disk?.percent);
 if(Number.isFinite(cpu)&&cpu>=90)warnings.push("CPU "+Math.round(cpu)+"%");
 if(Number.isFinite(ram)&&ram>=90)warnings.push("RAM "+Math.round(ram)+"%");
 if(Number.isFinite(disk)&&disk>=95)warnings.push("Disk "+Math.round(disk)+"%");
 const key=warnings.join(" · ");if(key&&key!==notificationState.resources)addNotification("warning","მაღალი სისტემური დატვირთვა","High system resource usage","რესურსების მაღალი გამოყენება: "+key,"High resource usage: "+key);
 notificationState.resources=key;saveNotifications();
}
function trackApiHealth(name,ok,detail){
 if(!["internet","hotspotClients","cellularStatus","resources","power"].includes(name))return;
 if(!ok&&!notificationApiErrors[name])addNotification("error","მონიტორინგის შეცდომა","Monitoring error",name+": "+String(detail||"მონაცემები მიუწვდომელია"),name+": "+String(detail||"Data unavailable"));
 if(ok&&notificationApiErrors[name])addNotification("success","მონიტორინგი აღდგა","Monitoring recovered",name+" მონაცემების მიღება აღდგა.",name+" data collection recovered.");
 notificationApiErrors[name]=!ok;
}
function trackSmsNotifications(messages){
 if(!Array.isArray(messages))return;const received=messages.filter(x=>x&&x.direction==="received"&&x.id!=null);
 if(!notificationState.smsReady){received.forEach(x=>notificationState.seenSms.add(String(x.id)));notificationState.smsReady=true;saveNotifications();return}
 received.forEach(sms=>{const id=String(sms.id);if(notificationState.seenSms.has(id))return;notificationState.seenSms.add(id);
 const number=String(sms.number||"უცნობი ნომერი"),raw=String(sms.text||"").trim(),preview=raw.length>180?raw.slice(0,177)+"...":raw;
 addNotification("sms","ახალი SMS","New SMS received","გამომგზავნი: "+number+(preview?" · "+preview:""),"From: "+number+(preview?" · "+preview:""))});
 saveNotifications();
}
function resetNotificationSession(){
 notificationState.items.length=0;notificationState.seenSms.clear();notificationState.smsReady=false;notificationState.clients=null;notificationState.hotspot=null;notificationState.internet=null;notificationState.cellular=null;notificationState.resources="";
 Object.keys(notificationApiErrors).forEach(k=>delete notificationApiErrors[k]);try{Object.values(notificationKeys).forEach(k=>sessionStorage.removeItem(k))}catch(_){}
 renderNotifications();setNotificationPanel(false);
}


/* DOM HELPERS */

function element(id) {
    return document.getElementById(id);
}

const technicalLabels = {
 platform:"პლატფორმა",system:"სისტემა",release:"ბირთვის ვერსია",architecture:"არქიტექტურა",hostname:"მოწყობილობის სახელი",python:"Python ვერსია",
 cpu_percent:"პროცესორის დატვირთვა",cpu_logical_cores:"ლოგიკური ბირთვები",cpu_physical_cores:"ფიზიკური ბირთვები",memory:"მეხსიერება",total_bytes:"სრული მოცულობა",available_bytes:"თავისუფალი მოცულობა",used_bytes:"გამოყენებული",percent:"გამოყენება",disk:"დისკი",path:"მონტაჟის წერტილი",free:"თავისუფალი",
 interfaces:"ინტერფეისები",interface:"ინტერფეისი",name:"სახელი",state:"მდგომარეობა",status:"სტატუსი",is_up:"აქტიურია",operstate:"ოპერაციული მდგომარეობა",mtu:"MTU",mac:"MAC მისამართი",mac_address:"MAC მისამართი",ipv4:"IPv4 მისამართები",ipv6:"IPv6 მისამართები",address:"მისამართი",netmask:"ქსელის ნიღაბი",gateway:"Gateway",gateways:"Gateway-ები",routes:"მარშრუტები",destination:"დანიშნულება",metric:"მეტრიკა",table:"ცხრილი",protocol:"პროტოკოლი",default:"ნაგულისხმევი მარშრუტი",
 driver:"დრაივერი",chipset:"ჩიპსეტი",vendor:"მწარმოებელი",product:"მოდელი",bus:"მიერთების ტიპი",phy:"PHY",phy_name:"PHY სახელი",bands:"სიხშირის დიაპაზონები",channels:"არხები",modes:"რეჟიმები",supported_modes:"მხარდაჭერილი რეჟიმები",frequencies:"სიხშირეები",channel:"არხი",frequency:"სიხშირე",ssid:"ქსელის სახელი (SSID)",bssid:"BSSID",signal:"სიგნალი",signal_dbm:"სიგნალი (dBm)",signal_percent:"სიგნალი (%)",security:"დაცვა",connected:"დაკავშირებულია",available:"ხელმისაწვდომია",enabled:"ჩართულია",disabled:"გამორთულია",read_only:"მხოლოდ წაკითხვა",
 assessment:"შეფასება",message:"შეტყობინება",error:"შეცდომა",access_points:"წვდომის წერტილები (AP)",clients:"Wi-Fi კლიენტები",concurrent_ap_client_observed:"AP + Client ერთდროულად",shared_phy_observed:"საერთო PHY",same_channel_observed:"ერთი არხი",forwarding:"ტრაფიკის გადაცემა",prerequisites:"წინაპირობები",ip_forward_enabled:"IP forwarding",firewall_tool:"Firewall სისტემა",firewall_tool_available:"Firewall ხელმისაწვდომია",dnsmasq_available:"dnsmasq ხელმისაწვდომია",rx_bytes:"მიღებული მონაცემები",tx_bytes:"გაგზავნილი მონაცემები",rx_packets:"მიღებული პაკეტები",tx_packets:"გაგზავნილი პაკეტები",total_rx_mbps:"ჩამოტვირთვის სიჩქარე",total_tx_mbps:"ატვირთვის სიჩქარე",technology:"ქსელის ტექნოლოგია",registration_state:"რეგისტრაციის მდგომარეობა",operator:"ოპერატორი",sim:"SIM ბარათი",modem:"მოდემი",modems:"მოდემები",network:"ქსელი",quality_percent:"სიგნალის ხარისხი",version:"ვერსია",service:"სერვისი",active_state:"აქტიური მდგომარეობა",sub_state:"ქვემდგომი მდგომარეობა",description:"აღწერა",manager:"მართვის სისტემა",dns_by_link:"DNS ინტერფეისების მიხედვით",dns_servers:"DNS სერვერები",type:"ტიპი",count:"რაოდენობა",boot_time:"ჩართვის დრო",uptime:"მუშაობის დრო"
};
Object.assign(technicalLabels, {
    device:"მოწყობილობა",resources:"სისტემური რესურსები",internetDetails:"ინტერნეტის ტექნიკური მონაცემები",
    cellularInterfaces:"მობილური ინტერფეისები",cellularDetails:"მოდემისა და SIM-ის მონაცემები",
    traffic:"ქსელური ტრაფიკი",capabilities:"მოწყობილობის შესაძლებლობები",interfaces:"ქსელის ინტერფეისები",
    wifiCapabilities:"Wi-Fi ადაპტერის შესაძლებლობები",routes:"მარშრუტიზაციის ცხრილი",wifi:"Wi-Fi კავშირი",
    hotspotPreflight:"Hotspot-ის შესაძლებლობები",repeaterStatus:"Repeater-ის დიაგნოსტიკა",
    dnsStatus:"DNS-ის მდგომარეობა",services:"სერვისები და ხელსაწყოები"
});
function technicalLabel(key) {
    const clean=String(key).replace(/_/g," ");
    return technicalLabels[key] || clean.charAt(0).toUpperCase()+clean.slice(1);
}
function technicalValue(value) {
    if(value===null || value===undefined || value==="") return "—";
    if(typeof value==="boolean") return value ? "დიახ" : "არა";
    if(typeof value==="number") return Number.isFinite(value) ? String(value) : "—";
    return String(value);
}
function buildTechnicalNode(value,depth) {
    if(Array.isArray(value)) {
        const wrap=document.createElement("div");
        wrap.className="technical-array";
        if(!value.length) {
            const empty=document.createElement("span");
            empty.className="technical-empty";
            empty.textContent="მონაცემები არ არის";
            wrap.appendChild(empty);
            return wrap;
        }
        value.forEach((item,index)=>{
            const card=document.createElement("article");
            card.className="technical-object";
            if(item && typeof item==="object") {
                const heading=document.createElement("h4");
                heading.className="technical-object-title";
                heading.textContent=item.name || item.interface || item.ssid || ("ჩანაწერი "+(index+1));
                card.append(heading,buildTechnicalNode(item,depth+1));
            } else {
                const val=document.createElement("span");
                val.className="technical-value";
                val.textContent=technicalValue(item);
                card.appendChild(val);
            }
            wrap.appendChild(card);
        });
        return wrap;
    }
    if(value && typeof value==="object") {
        const grid=document.createElement("div");
        grid.className="technical-grid";
        Object.entries(value).forEach(([key,item])=>{
            const field=document.createElement("div");
            field.className="technical-field";
            if(item && typeof item==="object") field.classList.add("wide");
            const label=document.createElement("span");
            label.className="technical-label";
            label.textContent=technicalLabel(key);
            field.appendChild(label);
            if(item && typeof item==="object" && depth<4) {
                field.appendChild(buildTechnicalNode(item,depth+1));
            } else {
                const val=document.createElement("span");
                val.className="technical-value";
                val.textContent=item && typeof item==="object" ? "[მონაცემები]" : technicalValue(item);
                if(typeof item==="boolean") val.classList.add("boolean");
                field.appendChild(val);
            }
            grid.appendChild(field);
        });
        return grid;
    }
    const val=document.createElement("span");
    val.className="technical-value";
    val.textContent=technicalValue(value);
    return val;
}
function showJSON(id,data) {
    const target=element(id);
    if(!target) return;
    target.textContent=typeof data==="string" ? data : JSON.stringify(data,null,2);
    const details=target.closest("details");
    if(!details) return;
    const viewId=id+"TechnicalView";
    let view=element(viewId);
    if(!view) {
        view=document.createElement("div");
        view.id=viewId;
        view.className="technical-panel";
        details.insertBefore(view,target);
    }
    target.classList.add("hidden");
    view.replaceChildren();
    const heading=document.createElement("div");
    heading.className="technical-panel-title";
    heading.textContent=technicalLabel(id);
    view.appendChild(heading);
    let parsed=data;
    if(typeof data==="string") {
        try { parsed=JSON.parse(data); } catch(_) { parsed=data; }
    }
    view.appendChild(buildTechnicalNode(parsed,0));
}

let repeaterCurrentConnection = null;

function renderRepeaterStatus(data) {
    showJSON("repeaterStatus", data);
    const interfaces = Array.isArray(data?.radio?.interfaces) ? data.radio.interfaces : [];
    const managed = interfaces.filter(item => item.type === "managed");
    const select = element("repeaterInterface");
    if (select) {
        const previous = select.value;
        select.replaceChildren(...managed.map(item => new Option(
            item.name + (item.ssid ? " · " + item.ssid : ""),
            item.name
        )));
        if (managed.some(item => item.name === previous)) select.value = previous;
        else if (managed.length) select.value = managed[0].name;
    }
    repeaterCurrentConnection = managed.find(item => item.ssid) || null;
    const status = element("repeaterConnection");
    const disconnect = element("repeaterDisconnectButton");
    const share = element("repeaterShareButton");
    if (status) {
        status.textContent = repeaterCurrentConnection
            ? tr("დაკავშირებულია: ", "Connected: ") + repeaterCurrentConnection.ssid
            : tr("Wi-Fi ქსელთან დაკავშირებული არ არის", "Not connected to a Wi-Fi network");
        status.className = "repeater-connection " + (repeaterCurrentConnection ? "online" : "offline");
    }
    if (disconnect) disconnect.disabled = !repeaterCurrentConnection;
    if (share) share.disabled = !repeaterCurrentConnection;
}


function setMetric(id, value, barId) {
    const safe = Math.max(0, Math.min(100, Number(value) || 0));
    element(id).textContent = safe.toFixed(1) + "%";
    element(barId).style.width = safe + "%";
}

function formatBytes(value) {
    const bytes = Number(value) || 0;

    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 ** 2) return (bytes / 1024).toFixed(2) + " KB";
    if (bytes < 1024 ** 3) return (bytes / 1024 ** 2).toFixed(2) + " MB";

    return (bytes / 1024 ** 3).toFixed(2) + " GB";
}

/* CSRF */

function getCookie(name) {
    const prefix = name + "=";
    const item = document.cookie.split("; ")
        .find(value => value.startsWith(prefix));

    if (!item) return null;

    return decodeURIComponent(item.substring(prefix.length));
}

/* AUTHENTICATION UI */

function showLogin(message = "") {
    if (typeof disconnectTerminal === "function") disconnectTerminal();
    const wasAuthenticated = state.authenticated;
    state.authenticated = false;
    stopPolling();
    if (wasAuthenticated) resetNotificationSession();
    element("notificationWrap")?.classList.add("hidden");

    document.body.classList.add("login-active");

    element("loginOverlay").classList.remove("hidden");
    element("dashboard").classList.add("hidden");
    element("logoutButton").classList.add("hidden");

    element("connection").textContent = "● Authentication required";
    element("loginError").textContent = message;
}

function showDashboard(username) {
    state.authenticated = true;

    document.body.classList.remove("login-active");

    element("loginOverlay").classList.add("hidden");
    element("dashboard").classList.remove("hidden");
    element("logoutButton").classList.remove("hidden");
    element("notificationWrap")?.classList.remove("hidden");

    element("connection").textContent = "● Connected: " + username;
}

/* AUTHENTICATED FETCH */

async function apiFetch(endpoint, options = {}) {
    const headers = new Headers(options.headers || {});
    const method = (options.method || "GET").toUpperCase();

    if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
        const csrfToken = getCookie("keyland_csrf");

        if (csrfToken) {
            headers.set("X-CSRF-Token", csrfToken);
        }
    }

    const response = await fetch(endpoint, {
        ...options,
        method,
        headers,
        credentials: "same-origin",
        cache: "no-store"
    });

    if (response.status === 401) {
        showLogin("Your session has expired. Please sign in again.");
        throw new Error("Authentication required");
    }

    return response;
}

async function fetchJSON(endpoint) {
    const response = await apiFetch(endpoint);

    if (!response.ok) {
        throw new Error("HTTP " + response.status);
    }

    return response.json();
}

function ztNode(tag, className, value) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== undefined && value !== null) node.textContent = String(value);
    return node;
}
function ztMetric(label, value, note) {
    const card = ztNode("div", "zt-metric");
    card.append(ztNode("span", "zt-metric-label", label), ztNode("strong", "zt-metric-value", value || "—"));
    if (note) card.append(ztNode("small", "zt-metric-note", note));
    return card;
}
function renderZeroTierStatus(data) {
    const info = data.info || {};
    const settings = info.config?.settings || {};
    const networks = Array.isArray(data.networks) ? data.networks : [];
    const peers = Array.isArray(data.peers) ? data.peers : [];
    const badge = element("zerotierAgentBadge");
    badge.textContent = info.online ? "ONLINE · აქტიური" : data.available ? "OFFLINE · კავშირი არ არის" : "NOT AVAILABLE";
    badge.className = "zt-state " + (info.online ? "zt-state-online" : "zt-state-offline");
    const status = element("zerotierStatus");
    status.replaceChildren(
        ztMetric("Node ID", info.address, "ამ მოწყობილობის იდენტიფიკატორი"),
        ztMetric("ZeroTier ვერსია", info.version, "ლოკალური აგენტი"),
        ztMetric("სერვისი", data.service, "zerotier-one"),
        ztMetric("ქსელები", networks.length, "ჩართული ქსელები"),
        ztMetric("Peers", peers.filter(p => p.role !== "PLANET").length, "მოწყობილობებთან კავშირები"),
        ztMetric("Listening port", settings.primaryPort, "ძირითადი პორტი")
    );
    const networkTarget = element("zerotierNetworks");
    networkTarget.replaceChildren();
    if (!networks.length) networkTarget.append(ztNode("p", "zt-empty", "ჩართული ქსელები ვერ მოიძებნა."));
    networks.forEach(network => {
        const card = ztNode("article", "zt-network-card");
        const head = ztNode("div", "zt-network-head");
        const title = ztNode("div");
        title.append(ztNode("strong", "zt-network-name", network.name || "სახელის გარეშე ქსელი"));
        title.append(ztNode("small", "zt-mono", network.nwid || network.id || "—"));
        head.append(title, ztNode("span", "zt-state " + (network.status === "OK" ? "zt-state-online" : "zt-state-warn"), network.status || "UNKNOWN"));
        card.append(head);
        const facts = ztNode("div", "zt-network-facts");
        [["IP მისამართი",(network.assignedAddresses || []).join(", ") || "—"],["ინტერფეისი",network.portDeviceName || "—"],["ტიპი",network.type || "—"],["DNS",(network.dns?.servers || []).join(", ") || "—"],["DNS დომენი",network.dns?.domain || "—"],["MTU",network.mtu || "—"]].forEach(([k,v]) => facts.append(ztNode("div", "", k + ": " + v)));
        card.append(facts);
        const routes = Array.isArray(network.routes) ? network.routes : [];
        if (routes.length) {
            const routeList = ztNode("div", "zt-route-list");
            routeList.append(ztNode("small", "zt-metric-label", "მარშრუტები"));
            routes.forEach(route => routeList.append(ztNode("div", "zt-route", (route.target || "—") + (route.via ? "  →  " + route.via : "  ·  პირდაპირი"))));
            card.append(routeList);
        }
        const use = ztNode("button", "secondary zt-use-network", "Network ID-ის გამოყენება");
        use.type = "button";
        use.addEventListener("click", () => {
            element("zerotierLocalNetworkId").value = network.nwid || network.id || "";
            element("zerotierLocalNetworkId").scrollIntoView({behavior:"smooth",block:"center"});
        });
        card.append(use);
        networkTarget.append(card);
    });
    const peerTarget = element("zerotierPeers");
    const peerCount = element("zerotierPeerCount");
    const nonPlanet = peers.filter(peer => peer.role !== "PLANET");
    peerCount.textContent = nonPlanet.length + " მოწყობილობა · " + peers.length + " სულ";
    peerTarget.replaceChildren();
    if (!peers.length) peerTarget.append(ztNode("p", "zt-empty", "Peer-ები ვერ მოიძებნა."));
    peers.forEach(peer => {
        const card = ztNode("article", "zt-peer-card");
        const head = ztNode("div", "zt-peer-head");
        head.append(ztNode("strong", "zt-mono", peer.address || "უცნობი ID"));
        head.append(ztNode("span", "zt-peer-role", peer.role || "PEER"));
        card.append(head);
        const preferred = (peer.paths || []).find(path => path.preferred) || (peer.paths || []).find(path => path.active);
        const facts = ztNode("div", "zt-peer-facts");
        facts.append(ztNode("span", "", peer.latency >= 0 ? peer.latency + " ms latency" : "Latency —"));
        facts.append(ztNode("span", "", "Paths: " + (peer.paths || []).length));
        facts.append(ztNode("span", "", preferred?.address || "აქტიური გზა —"));
        facts.append(ztNode("span", "", peer.version || "ვერსია —"));
        card.append(facts);
        peerTarget.append(card);
    });
    if (data.errors?.length) {
        const errors = ztNode("p", "zt-message zt-error", data.errors.join(" · "));
        status.append(errors);
    }
}
async function refreshZeroTier() {
    const target = element("zerotierStatus");
    if (!target || !state.authenticated) return;
    target.replaceChildren(ztNode("div", "zt-empty", "ZeroTier მონაცემები იტვირთება..."));
    try {
        const data = await fetchJSON(API + "/zerotier/status");
        renderZeroTierStatus(data);
    } catch (error) {
        target.replaceChildren(ztNode("div", "zt-message zt-error", "ZeroTier API შეცდომა: " + error.message));
        element("zerotierAgentBadge").textContent = "API შეცდომა";
        element("zerotierAgentBadge").className = "zt-state zt-state-offline";
        const networks = element("zerotierNetworks");
        const peers = element("zerotierPeers");
        const count = element("zerotierPeerCount");
        if (networks) networks.replaceChildren(ztNode("div", "zt-message zt-error", "ქსელების სია ვერ ჩაიტვირთა. შეამოწმე ადგილობრივი ZeroTier API."));
        if (peers) peers.replaceChildren(ztNode("div", "zt-message zt-error", "Peer-ების სია ვერ ჩაიტვირთა."));
        if (count) count.textContent = "მიუწვდომელია";
    }
    await refreshCentralNetworks();
}

async function submitZeroTierAction(path, body, phrase, warning, outputId) {
    if (!state.authenticated) return;
    if (!window.confirm(warning)) return;
    const typed = window.prompt("დადასტურებისთვის ზუსტად ჩაწერე: " + phrase);
    if (typed !== phrase) {
        element(outputId).textContent = "დადასტურება არ ემთხვევა. მოქმედება გაუქმებულია.";
        return;
    }
    const output = element(outputId);
    output.textContent = "მოთხოვნა იგზავნება...";
    try {
        const response = await apiFetch(API + path, {
            method: "POST", headers: {"Content-Type":"application/json"},
            body: JSON.stringify({...body, confirmed:true, confirmation_text:phrase})
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || ("HTTP " + response.status));
        output.textContent = data.output || data.message || "მოქმედება წარმატებით შესრულდა.";
        await refreshZeroTier();
    } catch (error) { output.textContent = "შეცდომა: " + error.message; }
}

async function refreshCentralNetworks() {
    const message = element("zerotierCentralMessage");
    const select = element("zerotierNetworkSelect");
    const members = element("zerotierMembers");
    if (!state.authenticated || !message || !select) return;
    message.textContent = "Central API ქსელები იტვირთება...";
    try {
        const data = await fetchJSON(API + "/zerotier/central/networks");
        if (!data.configured) {
            message.textContent = "Central API token არ არის დაყენებული. დაამატე KEYLAND_ZEROTIER_CENTRAL_TOKEN სერვერზე.";
            select.disabled = true;
            select.replaceChildren(new Option("Central API არ არის კონფიგურირებული", ""));
            if (members) members.textContent = "ქსელებისა და მოწყობილობების სიის სანახავად საჭიროა Central API token.";
            return;
        }
        if (!data.available) throw new Error(data.error || "Central API მიუწვდომელია");
        window.__keylandCentralNetworks = data.networks || [];
        const previous = select.value;
        const options = [new Option("აირჩიე ZeroTier ქსელი", "")];
        window.__keylandCentralNetworks.forEach(network => options.push(new Option(
            (network.name || "Unnamed network") + " · " + network.id +
            " · " + (network.total_member_count ?? 0) + " მოწყობილობა", network.id
        )));
        select.replaceChildren(...options);
        select.disabled = data.networks.length === 0;
        const selected = data.networks.find(n => n.id === previous) ||
            data.networks.find(n => n.id === (element("zerotierLocalNetworkId")?.value || ""));
        if (selected) select.value = selected.id;
        message.textContent = "Central API დაკავშირებულია · ქსელები: " + data.networks.length;
        if (select.value) await refreshCentralMembers();
        else if (members) members.textContent = "აირჩიე ქსელი წევრების სანახავად.";
    } catch (error) { message.textContent = "Central API შეცდომა: " + error.message; select.disabled = true; }
}

async function refreshCentralMembers() {
    const select = element("zerotierNetworkSelect");
    const target = element("zerotierMembers");
    if (!state.authenticated || !select || !target || !select.value) return;
    target.textContent = "მოწყობილობების სია იტვირთება...";
    try {
        const networkId = select.value;
        const data = await fetchJSON(API + "/zerotier/central/networks/" + encodeURIComponent(networkId) + "/members");
        if (!data.available) throw new Error(data.error || "წევრების სია მიუწვდომელია");
        const fragment = document.createDocumentFragment();
        (data.members || []).forEach(member => {
            const card = document.createElement("article"); card.className = "client-device";
            const head = document.createElement("div"); head.className = "client-device-head";
            const title = document.createElement("strong"); title.textContent = member.name || "სახელის გარეშე მოწყობილობა";
            const auth = document.createElement("span"); auth.className = "client-signal";
            auth.textContent = member.authorized === true ? "AUTHORIZED" : member.authorized === false ? "NOT AUTHORIZED" : "UNKNOWN";
            head.append(title, auth);
            const details = document.createElement("div"); details.className = "client-traffic";
            [
                ["Node ID", member.id || "—"], ["Managed IP", (member.ip_assignments || []).join(", ") || "—"],
                ["Physical address", member.physical_address || "—"],
                ["Last seen", member.last_seen ? new Date(Number(member.last_seen)).toLocaleString() : "—"],
                ["Client", member.client_version || "—"]
            ].forEach(([label, value]) => {
                const item = document.createElement("div"), small = document.createElement("small"), strong = document.createElement("b");
                small.textContent = label; strong.textContent = String(value); item.append(small, strong); details.append(item);
            });
            card.append(head, details);
            if (member.description) { const d = document.createElement("p"); d.className = "muted"; d.textContent = member.description; card.append(d); }
            const actions = document.createElement("div"); actions.className = "button-row";
            const action = document.createElement("button"); action.type = "button";
            action.className = member.authorized === true ? "danger" : "secondary";
            action.textContent = member.authorized === true ? "ავტორიზაციის გაუქმება" : "ავტორიზაცია";
            action.addEventListener("click", async () => {
                const verb = member.authorized === true ? "DEAUTHORIZE" : "AUTHORIZE";
                await submitZeroTierAction(
                    "/zerotier/central/networks/" + encodeURIComponent(networkId) + "/members/" +
                    encodeURIComponent(member.id) + (member.authorized === true ? "/deauthorize" : "/authorize"),
                    {}, verb + " " + member.id,
                    "ეს შეცვლის მოწყობილობის წვდომას ZeroTier ქსელზე. გავაგრძელოთ?",
                    "zerotierNetworkActionMessage"
                );
                await refreshCentralMembers();
            });
            actions.append(action); card.append(actions); fragment.append(card);
        });
        if (!data.members || !data.members.length) {
            const empty = document.createElement("p"); empty.className = "client-empty";
            empty.textContent = "ამ ქსელში წევრები ჯერ არ მოიძებნა."; fragment.append(empty);
        }
        target.replaceChildren(fragment);
        renderSelectedCentralNetwork();
    } catch (error) { target.textContent = "წევრების ჩატვირთვის შეცდომა: " + error.message; }
}

function renderSelectedCentralNetwork() {
    const id = element("zerotierNetworkSelect")?.value;
    const network = (window.__keylandCentralNetworks || []).find(item => item.id === id);
    if (!id || !network) return;
    element("zerotierNetworkConfig").textContent = JSON.stringify({
        id: network.id, name: network.name, private: network.private,
        routes: network.routes, ip_assignment_pools: network.ip_assignment_pools, dns: network.dns
    }, null, 2);
    element("zerotierNetworkName").value = network.name || "";
    element("zerotierNetworkPrivate").value = String(network.private !== false);
    element("zerotierDnsServers").value = (network.dns?.servers || []).join(", ");
    element("zerotierDnsDomain").value = network.dns?.domain || "";
    element("zerotierRoutes").value = JSON.stringify(network.routes || [], null, 2);
}

async function refreshZeroTierDiagnostics() {
    const target = element("zerotierDiagnostics");
    if (!target || !state.authenticated) return;
    target.textContent = "დიაგნოსტიკა იტვირთება...";
    try {
        const data = await fetchJSON(API + "/zerotier/diagnostics?lines=100");
        target.textContent = JSON.stringify(data, null, 2);
    } catch (error) { target.textContent = "დიაგნოსტიკის შეცდომა: " + error.message; }
}

element("zerotierRefreshButton")?.addEventListener("click", refreshZeroTier);
element("zerotierMembersRefreshButton")?.addEventListener("click", refreshCentralMembers);
element("zerotierNetworkRefreshButton")?.addEventListener("click", refreshCentralNetworks);
element("zerotierNetworkSelect")?.addEventListener("change", refreshCentralMembers);
element("zerotierDiagnosticsButton")?.addEventListener("click", refreshZeroTierDiagnostics);
element("zerotierJoinButton")?.addEventListener("click", () => {
    const id = element("zerotierLocalNetworkId").value.trim();
    submitZeroTierAction("/zerotier/local/join", {network_id:id}, "JOIN NETWORK " + id,
        "მოწყობილობა შეუერთდება მითითებულ ZeroTier ქსელს. გავაგრძელოთ?",
        "zerotierLocalActionMessage");
});
element("zerotierLeaveButton")?.addEventListener("click", () => {
    const id = element("zerotierLocalNetworkId").value.trim();
    submitZeroTierAction("/zerotier/local/leave", {network_id:id}, "LEAVE NETWORK " + id,
        "მოწყობილობა გამოეთიშება ქსელს. ZeroTier-ით მიმდინარე SSH კავშირიც შეიძლება გაწყდეს. გავაგრძელოთ?",
        "zerotierLocalActionMessage");
});
element("zerotierSaveNetworkButton")?.addEventListener("click", async () => {
    const id = element("zerotierNetworkSelect").value;
    if (!id) { element("zerotierNetworkActionMessage").textContent = "ჯერ აირჩიე ქსელი."; return; }
    let routes;
    try { routes = JSON.parse(element("zerotierRoutes").value || "[]"); }
    catch (_) { element("zerotierNetworkActionMessage").textContent = "Routes ველი უნდა იყოს სწორი JSON მასივი."; return; }
    const phrase = "UPDATE NETWORK " + id;
    const body = {
        name: element("zerotierNetworkName").value,
        private: element("zerotierNetworkPrivate").value === "true",
        dns_servers: element("zerotierDnsServers").value.split(",").map(x => x.trim()).filter(Boolean),
        dns_domain: element("zerotierDnsDomain").value.trim(), routes
    };
    await submitZeroTierAction("/zerotier/central/networks/" + encodeURIComponent(id) + "/settings",
        body, phrase, "შეიცვლება Central ქსელის სახელი, წვდომის რეჟიმი, DNS და მარშრუტები. გავაგრძელოთ?",
        "zerotierNetworkActionMessage");
    await refreshCentralNetworks();
});

/* SYSTEM POWER & RECOVERY */
async function submitSystemAction(path, phrase, warning) {
    if (!state.authenticated) return;
    if (!window.confirm(warning)) return;
    const typed = window.prompt("დადასტურებისთვის ზუსტად ჩაწერე: " + phrase);
    if (typed !== phrase) {
        element("systemActionMessage").textContent = "დადასტურება არ ემთხვევა. მოქმედება გაუქმებულია.";
        return;
    }
    const output = element("systemActionMessage");
    output.textContent = "მოთხოვნა იგზავნება...";
    try {
        const response = await apiFetch(API + path, {
            method: "POST",
            headers: {"Content-Type":"application/json"},
            body: JSON.stringify({confirmed:true, confirmation_text:phrase})
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || ("HTTP " + response.status));
        output.textContent = data.message || (data.success ? "მოქმედება დაგეგმილია." : "მოქმედება დასრულდა.");
    } catch (error) {
        output.textContent = "შეცდომა: " + error.message;
    }
}
document.addEventListener("DOMContentLoaded", () => {
    element("restartKeylandButton")?.addEventListener("click", () =>
        submitSystemAction("/system/power/restart-keyland", "RESTART KEYLAND",
            "გადაიტვირთება მხოლოდ Keyland-ის სერვისი. ქსელის მოწყობილობები არ უნდა გადაიტვირთოს. გავაგრძელოთ?"));
    element("rebootDeviceButton")?.addEventListener("click", () =>
        submitSystemAction("/system/power/reboot-device", "REBOOT DEVICE",
            "გადაიტვირთება მთელი OnePlus/Kali მოწყობილობა. ყველა კავშირი დროებით შეწყდება. გავაგრძელოთ?"));
    element("factoryResetButton")?.addEventListener("click", () =>
        submitSystemAction("/system/factory-reset", "RESET KEYLAND",
            "წაიშლება Keyland-ის შენახული Hotspot პარამეტრები. ჯერ შეიქმნება სარეზერვო ასლი. გავაგრძელოთ?"));
});

/* LOGIN */

async function performLogin(username, password) {
    const body = new URLSearchParams();
    body.set("username", username);
    body.set("password", password);

    const response = await fetch(API + "/auth/login", {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: {
            "Content-Type": "application/x-www-form-urlencoded"
        },
        body
    });

    if (!response.ok) {
        if (response.status === 401) {
            throw new Error("Incorrect username or password");
        }

        if (response.status === 403) {
            throw new Error("Login request rejected by security policy");
        }

        throw new Error("Login failed: HTTP " + response.status);
    }

    return response.json();
}

element("loginForm").addEventListener("submit", async event => {
    event.preventDefault();

    if (state.sessionCheckInProgress) return;

    const button = element("loginButton");
    const error = element("loginError");

    button.disabled = true;
    error.textContent = "";

    const username = element("loginUsername").value.trim();
    const password = element("loginპაროლი").value;

    try {
        const result = await performLogin(username, password);

        element("loginპაროლი").value = "";
        showDashboard(result.username);
        startPolling();

    } catch (err) {
        error.textContent = err.message;

    } finally {
        button.disabled = false;
    }
});

/* LOGOUT */

async function logout() {
    const button = element("logoutButton");
    button.disabled = true;

    try {
        const response = await apiFetch(API + "/auth/logout", {
            method: "POST"
        });

        if (!response.ok) {
            throw new Error("გასვლა failed: HTTP " + response.status);
        }

        showLogin("You have been logged out.");

    } catch (error) {
        console.error("გასვლა error:", error);

        if (state.authenticated) {
            element("connection").textContent = "გასვლა error";
        }

    } finally {
        button.disabled = false;
    }
}

element("logoutButton").addEventListener("click", logout);

/* SESSION CHECK */


async function loadServerVersion() {
    try {
        const health = await fetchJSON(API + "/health");
        element("serverVersion").textContent = "Keyland v" + (health.version || "—");
    } catch (_) {
        element("serverVersion").textContent = "API · offline";
    }
}
async function initializeApp() {
    if (state.sessionCheckInProgress) return;

    state.sessionCheckInProgress = true;
    loadServerVersion();

    try {
        const response = await fetch(API + "/auth/me", {
            credentials: "same-origin",
            cache: "no-store"
        });

        if (!response.ok) {
            showLogin();
            return;
        }

        const user = await response.json();

        showDashboard(user.username);
        startPolling();

    } catch (error) {
        console.error("Session check:", error);
        showLogin("Cannot connect to the server.");

    } finally {
        state.sessionCheckInProgress = false;
    }
}

/* NETWORK INTERFACES */

async function loadInterfaces() {
    if (!state.authenticated || state.interfaceLoading) return;

    state.interfaceLoading = true;

    const filter = element("interfaceFilter").value;

    const endpoint = filter === "active"
        ? API + "/network/interfaces?active_only=true"
        : API + "/network/interfaces";

    try {
        const data = await fetchJSON(endpoint);
        showJSON("interfaces", data);

    } catch (error) {
        if (state.authenticated) {
            element("interfaces").textContent =
                "Interface error: " + error.message;
        }

    } finally {
        state.interfaceLoading = false;
    }
}

/* INTERNET */

function updateInternet(data) {
    const status = element("internetStatus");
    if (data.connected === true) {
        status.textContent = "● Internet connected";
        status.className = "internet-status online";
    } else if (data.connected === false) {
        status.textContent = "● Internet unavailable";
        status.className = "internet-status offline";
    } else {
        status.textContent = "● Status unknown";
        status.className = "internet-status unknown";
    }

    const panel = element("internetPublicDetails");
    if (panel) {
        const fields = [
            ["გარე IPv4 (Public IP)", data.public_ipv4],
            ["გარე IPv6", data.public_ipv6],
            ["WAN წყარო", data.wan_type === "cellular" ? "მობილური ინტერნეტი / LTE" :
                data.wan_type === "wifi" ? "Wi-Fi" :
                data.wan_type === "ethernet_or_other" ? "Ethernet / სხვა" : "უცნობია"],
            ["WAN ინტერფეისი", data.wan_interface],
            ["ლოკალური IPv4", data.local_ipv4],
            ["Gateway", data.gateway]
        ];
        panel.replaceChildren();
        fields.forEach(([label, value]) => {
            const field = document.createElement("div");
            field.className = "technical-field";
            const title = document.createElement("span");
            title.className = "technical-label";
            title.textContent = label;
            const content = document.createElement("strong");
            content.className = "technical-value";
            content.textContent = value || "—";
            field.append(title, content);
            panel.appendChild(field);
        });
    }
    showJSON("internetDetails", data);
}

/* GENERAL TRAFFIC */

function updateTraffic(data) {
    element("totalRx").textContent =
        Number(data.total_rx_mbps || 0).toFixed(4) + " Mbps";

    element("totalTx").textContent =
        Number(data.total_tx_mbps || 0).toFixed(4) + " Mbps";

    showJSON("traffic", data);
}

/* CELLULAR */

function updateCellular(status, usage) {
    const technology = element("cellularTechnology");
    const modems = status.modems || [];
    const modem = modems.find(item => item.available) || modems[0];

    if (!status.available) {
        technology.textContent = "UNAVAILABLE";
        element("simStatus").textContent = "Unavailable";
        element("modemStatus").textContent =
            status.error || "ModemManager unavailable";
        element("cellularOperator").textContent = "--";
        element("registrationStatus").textContent = "--";
        element("cellularSignal").textContent = "--";
        element("cellularSignalBar").style.width = "0%";
        element("cellularConnection").textContent = "--";
        showJSON("cellularInterfaces", status.interfaces || []);
        showJSON("cellularDetails", { ...status, usage });
        element("cellularRx").textContent = "--";
        element("cellularTx").textContent = "--";
        return;
    }

    if (!modem) {
        technology.textContent = "NO MODEM";
        element("simStatus").textContent = "Not detected";
        element("modemStatus").textContent = "No modem detected";
        element("cellularOperator").textContent = "--";
        element("registrationStatus").textContent = "--";
        element("cellularSignal").textContent = "--";
        element("cellularSignalBar").style.width = "0%";
    } else {
        const sim = modem.sim || {};
        const network = modem.network || {};
        const signal = modem.signal || {};
        const operator = modem.operator || {};

        technology.textContent =
            network.technology || "UNKNOWN";

        element("simStatus").textContent =
            sim.detected
                ? (sim.state || "Detected")
                : "Not detected";

        element("modemStatus").textContent =
            modem.state || "Unknown";

        element("cellularOperator").textContent =
            operator.name || operator.code || "Unknown";

        element("registrationStatus").textContent =
            network.registration_state || "Unknown";

        const quality = signal.quality_percent;

        if (quality !== null && quality !== undefined) {
            const safe = Math.max(0, Math.min(100, Number(quality)));
            element("cellularSignal").textContent =
                safe.toFixed(0) + "%";

            element("cellularSignalBar").style.width =
                safe + "%";
        } else {
            element("cellularSignal").textContent = "Unknown";
            element("cellularSignalBar").style.width = "0%";
        }
    }

    const interfaces = status.interfaces || [];

    const connected = interfaces.some(item =>
        item.is_up === true &&
        ((item.ipv4 || []).length > 0)
    );

    element("cellularConnection").textContent =
        connected ? "Interface active" : "Not confirmed";

    showJSON("cellularInterfaces", interfaces);

    const usageInterfaces = usage.interfaces || [];

    const rx = usageInterfaces.reduce(
        (sum, item) => sum + (Number(item.rx_bytes) || 0), 0
    );

    const tx = usageInterfaces.reduce(
        (sum, item) => sum + (Number(item.tx_bytes) || 0), 0
    );

    element("cellularRx").textContent = formatBytes(rx);
    element("cellularTx").textContent = formatBytes(tx);

    showJSON("cellularDetails", { ...status, usage });
}

/* TRAFFIC CHART */

function drawTrafficChart(samples) {
    const canvas = element("trafficChart");
    const rect = canvas.getBoundingClientRect();

    if (!rect.width || !rect.height) return;

    const ratio = window.devicePixelRatio || 1;

    canvas.width = Math.round(rect.width * ratio);
    canvas.height = Math.round(rect.height * ratio);

    const ctx = canvas.getContext("2d");

    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);

    const width = rect.width;
    const height = rect.height;

    const left = 48;
    const right = 10;
    const top = 12;
    const bottom = 25;

    const chartWidth = width - left - right;
    const chartHeight = height - top - bottom;

    ctx.clearRect(0, 0, width, height);

    ctx.font = "11px Arial";
    ctx.fillStyle = "#8d9bb0";
    ctx.strokeStyle = "#30394a";
    ctx.lineWidth = 1;

    const values = samples.flatMap(sample => [
        Number(sample.rx_mbps) || 0,
        Number(sample.tx_mbps) || 0
    ]);

    const maxValue = Math.max(0.1, ...values) * 1.2;

    for (let i = 0; i <= 4; i++) {
        const y = top + chartHeight * i / 4;
        const label = (maxValue * (1 - i / 4)).toFixed(3);

        ctx.beginPath();
        ctx.moveTo(left, y);
        ctx.lineTo(width - right, y);
        ctx.stroke();

        ctx.fillText(label, 2, y + 4);
    }

    if (!samples.length) {
        ctx.fillStyle = "#8d9bb0";
        ctx.fillText(
            "Waiting for traffic samples...",
            left + 10,
            top + chartHeight / 2
        );
        return;
    }

    function drawLine(key, color) {
        ctx.beginPath();
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;

        samples.forEach((sample, index) => {
            const x = samples.length === 1
                ? left + chartWidth
                : left + index / (samples.length - 1) * chartWidth;

            const value = Number(sample[key]) || 0;
            const y = top + chartHeight -
                (value / maxValue) * chartHeight;

            if (index === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });

        ctx.stroke();
    }

    drawLine("rx_mbps", "#50d69b");
    drawLine("tx_mbps", "#78aaff");

    ctx.fillStyle = "#8d9bb0";
    ctx.fillText("Oldest", left, height - 5);
    ctx.fillText("Latest", width - right - 35, height - 5);
}

async function loadTrafficHistory() {
    const data = await fetchJSON(API + "/network/traffic/history");

    if (state.authenticated) {
        drawTrafficChart(data.samples || []);
    }
}

/* SYSTEM RESOURCES */

function updateResources(data) {
    if (!data) return;

    if (data.memory) {
        setMetric("ram", data.memory.percent, "ramBar");
    }

    setMetric("cpu", data.cpu_percent, "cpuBar");

    if (data.disk) {
        setMetric("disk", data.disk.percent, "diskBar");
    }

    element("cores").textContent =
        data.cpu_logical_cores ?? "--";
}


function renderPowerStatus(data) {
    const battery = data && data.battery;
    const chargers = data && Array.isArray(data.chargers) ? data.chargers : [];
    const charger = chargers.find(item => item.online === true) || chargers[0] || null;
    const setText = (id, value) => {
        const target = element(id);
        if (target) target.textContent = value == null || value === "" ? "—" : String(value);
    };
    const fmt = (value, unit, digits = 1) =>
        value == null ? "—" : Number(value).toFixed(digits) + unit;

    if (!data || !data.available || !battery) {
        setText("batteryStatus", (data && data.error) || "ელემენტი ვერ მოიძებნა");
        setText("batteryLevel", "—");
        setText("batteryVoltage", "—");
        setText("batteryTemperature", "—");
        setText("batteryCurrent", "—");
        return;
    }

    setText("batteryStatus", battery.status || "Unknown");
    setText("batteryLevel", battery.capacity_percent == null ? "—" : battery.capacity_percent + "%");
    setText("batteryVoltage", fmt(battery.voltage_v, " V", 3));
    setText("batteryTemperature", fmt(battery.temperature_c, " °C"));
    setText("batteryCurrent", fmt(battery.current_ma, " mA"));
    setText("batteryChargeNow", fmt(battery.charge_now_mah, " mAh"));
    setText("batteryChargeFull",
        fmt(battery.charge_full_mah, " mAh") + " / " +
        fmt(battery.charge_full_design_mah, " mAh"));
    setText("batteryHealth", battery.health || "დამტენზე არ არის მოწოდებული");
    setText("batteryTechnology",
        [battery.technology, battery.manufacturer].filter(Boolean).join(" · ") || "—");

    const bar = element("batteryLevelBar");
    if (bar) {
        const level = Number(battery.capacity_percent);
        bar.style.width = Number.isFinite(level) ? Math.max(0, Math.min(100, level)) + "%" : "0%";
    }

    setText("chargerType", charger
        ? charger.type + (charger.online === true ? " · მიერთებულია" : " · არააქტიური")
        : "დამტენი არ არის");
    setText("chargerVoltage", charger ? fmt(charger.voltage_v, " V", 3) : "—");
    setText("chargerCurrent", charger ? fmt(charger.current_ma, " mA") : "—");
    setText("chargerPower", charger ? fmt(charger.power_mw, " mW") : "—");
    showJSON("batteryDetails", data);
}

/* MAIN DASHBOARD */

async function renderWifiLive(data) {
    const target = element("wifiLiveStatus");
    if (!target) return;
    const links = Array.isArray(data.connections) ? data.connections : [];
    const active = links.find(item => item.connected && item.type === "AP" && item.frequency_mhz) || links.find(item => item.connected && item.frequency_mhz);
    if (!active) {
        target.textContent = "Wi-Fi · კავშირი არ არის";
        target.title = "აქტიური Wi-Fi სიხშირე ამჟამად მიუწვდომელია";
        return;
    }
    const frequency = Number(active.frequency_mhz);
    const band = active.band || (frequency >= 5955 ? "6 GHz" : frequency >= 4900 ? "5 GHz" : "2.4 GHz");
    const signal = active.signal_dbm != null ? " · " + active.signal_dbm + " dBm" : "";
    target.textContent = band + " · CH " + (active.channel ?? "—") + " · " + frequency + " MHz" + signal;
    target.title = "ინტერფეისი: " + active.interface + (active.ssid ? " · SSID: " + active.ssid : "");
}

async function loadData() {
    if (!state.authenticated || state.loading) return;

    state.loading = true;

    const button = element("refreshButton");
    button.disabled = true;

    element("connection").textContent = "Refreshing...";

    const jobs = [
        ["device", API + "/device"],
        ["capabilities", API + "/capabilities"],
        ["routes", API + "/network/routes"],
        ["wifi", API + "/wifi"],
        ["wifiCapabilities", API + "/wifi/capabilities"],
        ["repeaterStatus", API + "/wifi/repeater/status"],
        ["hotspotStatus", API + "/wifi/hotspot/router/status"],
        ["hotspotClients", API + "/wifi/hotspot/router/clients"],
        ["services", API + "/services"],
        ["resources", API + "/system/resources"],
        ["power", API + "/system/power"],
        ["traffic", API + "/network/traffic"],
        ["internet", API + "/network/internet"],
        ["cellularStatus", API + "/cellular/status"],
        ["cellularUsage", API + "/cellular/usage"],
        ["zerotierStatus", API + "/zerotier/status"]
    ];

    try {
        const results = await Promise.allSettled(
            jobs.map(async ([name, url]) => {
                const data = await fetchJSON(url);

                if (!state.authenticated) return { name, data };

                switch (name) {
                    case "wifi":
                        renderWifiLive(data);
                        showJSON("wifi", data);
                        break;

                    case "traffic":
                        updateTraffic(data);
                        break;

                    case "internet":
                        trackInternetNotification(data);
                        updateInternet(data);
                        break;

                    case "resources":
                        updateResources(data);
                        showJSON("resources", data);
                        break;
                    case "power":
                        renderPowerStatus(data);
                        break;
                    case "repeaterStatus":
                        renderRepeaterStatus(data);
                        break;

                    case "hotspotClients":
                        trackHotspotNotifications(data);
                        renderHotspotClients(data);
                        break;

                    case "hotspotDnsQueries":
                        renderHotspotDnsQueries(data);
                        break;

                    default:
                        if (name !== "cellularStatus" &&
                            name !== "cellularUsage") {
                            showJSON(name, data);
                        }
                }

                return { name, data };
            })
        );

        if (!state.authenticated) return;

        const dataByName = {};
        let failures = 0;

        results.forEach((result, index) => {
            trackApiHealth(jobs[index][0], result.status === "fulfilled", result.status === "rejected" ? result.reason?.message : "");
            if (result.status === "fulfilled") {
                dataByName[result.value.name] = result.value.data;
            } else {
                failures++;

                const name = jobs[index][0];

                if (name === "power") {
                    const status = element("batteryStatus");
                    if (status) status.textContent = "შეცდომა: " + result.reason.message;
                } else if (name === "cellularStatus" ||
                    name === "cellularUsage") {
                    showJSON("cellularDetails", { error: name + ": " + result.reason.message });
                } else if (name === "hotspotDnsQueries") {
                    const list = element("hotspotDnsQueries");
                    const count = element("hotspotDnsCount");
                    if (count) count.textContent = tr("API შეცდომა", "API error");
                    if (list) {
                        const message = document.createElement("div");
                        message.className = "client-empty";
                        message.textContent = tr(
                            "DNS ისტორიის API მიუწვდომელია (" + result.reason.message + "). განაახლე Keyland backend v1.0.0-ზე და გადაამოწმე systemd სერვისი.",
                            "DNS history API is unavailable (" + result.reason.message + "). Deploy the Keyland v1.0.0 backend and verify the systemd service."
                        );
                        list.replaceChildren(message);
                    }
                } else {
                    const target = element(name);

                    if (target) {
                        target.textContent =
                            "Error: " + result.reason.message;
                    }
                }
            }
        });

        if (dataByName.cellularStatus) {
            trackCellularNotification(dataByName.cellularStatus);
            updateCellular(
                dataByName.cellularStatus,
                dataByName.cellularUsage || { interfaces: [] }
            );
        }
        if (dataByName.resources) trackResourceNotifications(dataByName.resources);
        if (dataByName.zerotierStatus) refreshCentralNetworks();
        refreshCentralNetworks();

        const extraResults = await Promise.allSettled([
            loadInterfaces(),
            loadTrafficHistory(),
            loadWlan0Wan()
        ]);

        extraResults.forEach(result => {
            if (result.status === "rejected") failures++;
        });

        if (!state.authenticated) return;

        element("connection").textContent =
            failures === 0 ? "● Connected" : "● Partial data";

        element("updated").textContent =
            "Updated: " + new Date().toLocaleTimeString();

    } catch (error) {
        if (state.authenticated) {
            element("connection").textContent =
                "Connection error: " + error.message;
        }

    } finally {
        state.loading = false;
        button.disabled = false;
    }
}


/* Georgian / English interface */
const translations = {
 "ქსელის მართვის ერთიანი პანელი":"Network Management Console",
 "ადმინისტრატორის სახელი":"Administrator username",
 "პაროლი":"Password",
 "შესვლა":"Sign in",
 "მხოლოდ ადმინისტრატორისთვის":"Administrators only",
 "გასვლა":"Sign out",
 "კონფიდენციალურობა: HTTPS კავშირების შიგთავსს, პირად მიმოწერას, პაროლებს ან ვებგვერდების შიდა შიგთავსს Keyland არ კითხულობს და არ იწერს. DNS მონიტორინგი, ჩართვის შემთხვევაში, აჩვენებს მხოლოდ DNS მოთხოვნების მეტამონაცემებს (დომენს, დროსა და მოთხოვნის ტიპს) და მხოლოდ მაშინ, როცა მოწყობილობის DNS მოთხოვნები Keyland-ის DNS სერვერზე გადის. VPN-მა, დაშიფრულმა DNS-მა ან სხვა გარე resolver-მა შეიძლება ეს ხილვადობა შეზღუდოს.":"Privacy: Keyland does not read or record HTTPS content, private messages, passwords, or webpage contents. If enabled, DNS monitoring shows only DNS query metadata (domain, time, and query type), and only when the device sends DNS queries through Keyland’s DNS server. VPNs, encrypted DNS, or other external resolvers may limit this visibility.",
 "მიმოხილვა":"Overview",
 "მობილური ქსელი":"Mobile network",
 "Wi-Fi და ადაპტერები":"Wi-Fi & adapters",
 "სისტემა":"System",
 "მონაცემების განახლება":"Refresh data",
 "Wi-Fi ქსელების ძიება":"Scan Wi-Fi networks",
 "ჯერ არ განახლებულა":"Not updated yet",
 "მოწყობილობა":"Device",
 "სისტემური რესურსები":"System resources",
 "პროცესორის დატვირთვა":"CPU usage",
 "ოპერატიული მეხსიერება":"Memory usage",
 "დისკის გამოყენება":"Disk usage",
 "პროცესორის ნაკადები":"CPU threads",
 "ინტერნეტის მდგომარეობა":"Internet status",
 "SIM / მობილური ქსელი":"SIM / Mobile network",
 "მოწყობილობის შესაძლებლობები":"Device capabilities",
 "ქსელის ინტერფეისები":"Network interfaces",
 "Wi-Fi ადაპტერის შესაძლებლობები":"Wi-Fi adapter capabilities",
 "რეპიტერის რეჟიმის მზადყოფნა":"Repeater readiness",
 "მარშრუტიზაციის ცხრილი":"Routing table",
 "Wi-Fi კავშირი":"Wi-Fi connection",
 "ახლომდებარე Wi-Fi ქსელები":"Nearby Wi-Fi networks",
 "DNS-ის მართვა":"DNS management",
 "ქსელის ინტერფეისი":"Network interface",
 "DNS სერვერები (მძიმით გამოყოფილი)":"DNS servers (comma-separated)",
 "DNS სერვერები (არასავალდებულო)":"DNS servers (optional)",
 "DNS-ის გამოყენება":"Apply DNS",
 "ავტომატურზე დაბრუნება":"Restore automatic",
 "მდგომარეობის ნახვა":"Refresh status",
 "Wi-Fi ადაპტერები და ჩიპსეტები":"Wi-Fi adapters & chipsets",
 "WAN / LAN და DHCP":"WAN / LAN & DHCP",
 "Firewall და NAT":"Firewall & NAT",
 "SIM / LTE / 5G პარამეტრები":"SIM / LTE / 5G settings",
 "ქსელთან დაკავშირებული მოწყობილობები":"Connected devices",
 "სისტემა, ჟურნალები და აღდგენა":"System, logs & recovery",
 "შესაძლებლობების შემოწმება":"Check capabilities",
 "ცალკე AP ინტერფეისის შექმნა":"Create dedicated AP interface",
 "საწყისი Wi-Fi ინტერფეისი (ვირტუალური AP-ის შესაქმნელად)":"Source Wi-Fi interface (to create virtual AP)",
 "ინტერფეისის არჩევა":"Select interface",
 "Hotspot-ის ჩართვა":"Start hotspot",
 "Hotspot-ის გამორთვა":"Stop hotspot",
 "AP ინტერფეისი":"AP interface",
 "Hotspot-ის სახელი (SSID)":"Hotspot name (SSID)",
 "უსაფრთხოება":"Security",
 "პაროლი (8–63 სიმბოლო)":"Password (8–63 characters)",
 "SIM, ModemManager, LTE/4G/5G and mobile interface monitoring.":"SIM-ის, ModemManager-ის, LTE/4G/5G-ისა და მობილური ინტერფეისების მონიტორინგი.",
 "SIM-ის სტატუსი":"SIM-ის სტატუსი",
 "OPERATOR":"ოპერატორი",
 "მოდემის სტატუსი":"მოდემის სტატუსი",
 "Loading...":"იტვირთება...",
 "Scanning...":"სკანირება...",
 "Supported Wi-Fi modes, bands, channels and interfaces.":"Wi-Fi-ის მხარდაჭერილი რეჟიმები, დიაპაზონები, არხები და ინტერფეისები.",
 "Read-only inspection of AP + Client and forwarding readiness.":"AP + Client რეჟიმისა და ტრაფიკის გადაცემის მზადყოფნის მხოლოდ წაკითხვადი შემოწმება.",
 "Press Scan Wi-Fi networks":"დააჭირე Wi-Fi ქსელების ძიებას",
 "Connection error: ":"კავშირის შეცდომა: ",
 "მობილური ქსელი":"Mobile network",
 "სისტემის მიმოხილვა":"System overview",
 "მოწყობილობის, ინტერნეტისა და რესურსების მიმდინარე მდგომარეობა":"Current device, internet and resource status",
 "ინტერნეტთან კავშირი":"Internet connection",
 "მობილური ინტერფეისები / IP მისამართები":"Cellular interfaces / IP addresses",
 "ქსელური ტრაფიკი":"Network traffic",
 "ჩამოტვირთვა RX":"Download RX",
 "ატვირთვა TX":"Upload TX",
 "მიღება RX":"Receive RX",
 "გაგზავნა TX":"Transmit TX",
 "ბოლო 60 ჩანაწერი":"Last 60 samples",
 "ინტერფეისის ფილტრი:":"Interface filter:",
 "ყველა ინტერფეისი":"All interfaces",
 "მხოლოდ აქტიური":"Active only",
 "Wi-Fi ქსელი":"Wi-Fi network",
 "ადაპტერის შესაძლებლობები, კავშირები და უსადენო წვდომა":"Adapter capabilities, connections and wireless access",
 "ახლომდებარე Wi-Fi ქსელები":"Nearby Wi-Fi networks",
 "დამატებითი პარამეტრები და დიაგნოსტიკა":"Advanced settings and diagnostics",
 "როუტერის რეჟიმი":"Router mode",
 "ინტერნეტის წყარო (WAN)":"Internet source (WAN)",
 "პაროლი (8–63 ASCII სიმბოლო)":"Password (8–63 ASCII characters)",
 "Hotspot-ის დაკავშირებული მოწყობილობები":"Hotspot connected devices",
 "ჩამოტვირთული":"Downloaded",
 "ატვირთული":"Uploaded",
 "დაბლოკვა":"Block",
 "განბლოკვა":"Unblock",
 "სიჩქარის ლიმიტი":"Speed limit",
 "ტექნიკური დეტალები":"Technical details",
 "მობილური ქსელის მართვა":"Mobile network management",
 "ტექნიკური დიაგნოსტიკა":"Technical diagnostics",
 "ინტერფეისები":"Interfaces",
 "ქსელის მდგომარეობა":"Network status",
 "სერვისები / ხელსაწყოები":"Services / Tools",
 "მდგომარეობა განახლებულია.":"Status updated.",
 "სესიის შემოწმება...":"Checking session...",
 "სისტემის მართვის პანელი":"System control panel",
 "მობილური ჩამოტვირთვა":"Mobile download",
 "მობილური ატვირთვა":"Mobile upload",
 "სიგნალის ხარისხი":"Signal quality",
 "მოდემის სტატუსი":"Modem status",
 "რეგისტრაცია":"Registration",
 "ოპერატორი":"Operator",
 "SIM-ის სტატუსი":"SIM status"

};
Object.assign(translations, {
 "პლატფორმა":"Platform","სისტემა":"System","ბირთვის ვერსია":"Kernel version","არქიტექტურა":"Architecture","მოწყობილობის სახელი":"Hostname","Python ვერსია":"Python version",
 "პროცესორის დატვირთვა":"CPU usage","ლოგიკური ბირთვები":"Logical cores","ფიზიკური ბირთვები":"Physical cores","მეხსიერება":"Memory","სრული მოცულობა":"Total capacity","თავისუფალი მოცულობა":"Available capacity","გამოყენებული":"Used","გამოყენება":"Usage","დისკი":"Disk","მონტაჟის წერტილი":"Mount point","ინტერფეისები":"Interfaces","ინტერფეისი":"Interface","სახელი":"Name","მდგომარეობა":"State","სტატუსი":"Status","აქტიურია":"Active","ოპერაციული მდგომარეობა":"Operational state","MAC მისამართი":"MAC address","IPv4 მისამართები":"IPv4 addresses","IPv6 მისამართები":"IPv6 addresses","მისამართი":"Address","ქსელის ნიღაბი":"Netmask","მარშრუტები":"Routes","დანიშნულება":"Destination","მეტრიკა":"Metric","ცხრილი":"Table","პროტოკოლი":"Protocol","ნაგულისხმევი მარშრუტი":"Default route",
 "დრაივერი":"Driver","ჩიპსეტი":"Chipset","მწარმოებელი":"Vendor","მოდელი":"Model","მიერთების ტიპი":"Bus type","PHY":"PHY","PHY სახელი":"PHY name","სიხშირის დიაპაზონები":"Frequency bands","არხები":"Channels","რეჟიმები":"Modes","მხარდაჭერილი რეჟიმები":"Supported modes","სიხშირეები":"Frequencies","არხი":"Channel","სიხშირე":"Frequency","ქსელის სახელი (SSID)":"Network name (SSID)","სიგნალი":"Signal","დაცვა":"Security","ხელმისაწვდომია":"Available","ჩართულია":"Enabled","გამორთულია":"Disabled","მხოლოდ წაკითხვა":"Read-only",
 "შეფასება":"Assessment","შეტყობინება":"Message","შეცდომა":"Error","წვდომის წერტილები (AP)":"Access points (AP)","Wi-Fi კლიენტები":"Wi-Fi clients","AP + Client ერთდროულად":"AP + Client observed simultaneously","საერთო PHY":"Shared PHY","ერთი არხი":"Same channel","ტრაფიკის გადაცემა":"Forwarding","წინაპირობები":"Prerequisites","Firewall სისტემა":"Firewall backend","Firewall ხელმისაწვდომია":"Firewall available","dnsmasq ხელმისაწვდომია":"dnsmasq available","მიღებული მონაცემები":"Received bytes","გაგზავნილი მონაცემები":"Transmitted bytes","ჩამოტვირთვის სიჩქარე":"Download rate","ატვირთვის სიჩქარე":"Upload rate","ქსელის ტექნოლოგია":"Network technology","რეგისტრაციის მდგომარეობა":"Registration state","ოპერატორი":"Operator","SIM ბარათი":"SIM card","მოდემი":"Modem","მოდემები":"Modems","ქსელი":"Network","ვერსია":"Version","სერვისი":"Service","აქტიური მდგომარეობა":"Active state","ქვემდგომი მდგომარეობა":"Sub-state","აღწერა":"Description","მართვის სისტემა":"Manager","DNS ინტერფეისების მიხედვით":"DNS by interface","DNS სერვერები":"DNS servers","ტიპი":"Type","რაოდენობა":"Count","ჩართვის დრო":"Boot time","მუშაობის დრო":"Uptime",
 "მოწყობილობა":"Device","სისტემური რესურსები":"System resources","ინტერნეტის ტექნიკური მონაცემები":"Internet diagnostics","მობილური ინტერფეისები":"Cellular interfaces","მოდემისა და SIM-ის მონაცემები":"Modem and SIM details","ქსელური ტრაფიკი":"Network traffic","მოწყობილობის შესაძლებლობები":"Device capabilities","ქსელის ინტერფეისები":"Network interfaces","Wi-Fi ადაპტერის შესაძლებლობები":"Wi-Fi adapter capabilities","მარშრუტიზაციის ცხრილი":"Routing table","Wi-Fi კავშირი":"Wi-Fi connection","Hotspot-ის შესაძლებლობები":"Hotspot capabilities","Repeater-ის დიაგნოსტიკა":"Repeater diagnostics","DNS-ის მდგომარეობა":"DNS status","სერვისები და ხელსაწყოები":"Services and tools"
});
Object.assign(translations, {
 "ქსელების ძიება":"Scan networks",
 "Wi-Fi ადაპტერი":"Wi-Fi adapter",
 "არჩეული ქსელის პაროლი":"Selected network password",
 "დაკავშირება":"Connect",
 "დაკავშირებულია: ":"Connected: ",
 "Wi-Fi ქსელთან დაკავშირებული არ არის":"Not connected to a Wi-Fi network",
 "ჯერ მოძებნე ახლომდებარე Wi-Fi ქსელები.":"Scan for nearby Wi-Fi networks first.",
 "ქსელების სია ჯერ არ მოძებნილა.":"Networks have not been scanned yet.",
 "დამატებითი დიაგნოსტიკა":"Advanced diagnostics",
 "სისტემის დიაგნოსტიკა":"System diagnostics",
 "მდგომარეობა და დიაგნოსტიკა":"Status and diagnostics",
 "ინტერნეტის გაზიარება":"Share internet",
 "ქსელთან დაკავშირება მიმდინარეობს...":"Connecting to Wi-Fi...",
 "კავშირის მდგომარეობა მოწმდება...":"Checking connection status..."
});
Object.assign(translations, {"შეტყობინებები":"Notifications","ყველას წაკითხულად მონიშვნა":"Mark all as read","გასუფთავება":"Clear","შეტყობინებები ჯერ არ არის.":"No notifications yet."});
const reverseTranslations = Object.fromEntries(Object.entries(translations).map(([ka, en]) => [en, ka]));
const englishToGeorgian = Object.fromEntries(Object.entries(translations).map(([ka, en]) => [en, ka]));
Object.assign(englishToGeorgian, {"SIM-ის სტატუსი":"SIM-ის მდგომარეობა","მოდემის სტატუსი":"მოდემის მდგომარეობა","REGISTRATION":"ქსელში რეგისტრაცია","სიგნალის ხარისხი":"სიგნალის ხარისხი","MOBILE CONNECTION":"მობილური კავშირი","MOBILE DOWNLOAD":"მობილური ჩამოტვირთვა","MOBILE UPLOAD":"მობილური ატვირთვა","CELLULAR INTERFACES / IP ADDRESSES":"მობილური ინტერფეისები / IP მისამართები","connected":"დაკავშირებულია","disconnected":"გათიშულია","home":"სახლის ქსელი","detected":"აღმოჩენილია","unknown":"უცნობია","enabled":"ჩართულია","disabled":"გამორთულია","active":"აქტიურია","inactive":"არააქტიურია","Services / Tools":"სერვისები და ხელსაწყოები","Technical details":"ტექნიკური დეტალები","Loading...":"იტვირთება...","Supported Wi-Fi modes, bands, channels and interfaces.":"Wi-Fi-ის მხარდაჭერილი რეჟიმები, დიაპაზონები, არხები და ინტერფეისები.","Press Wi-Fi ქსელების ძიება":"დააჭირე ღილაკს Wi-Fi ქსელების მოსაძებნად","Router mode":"როუტერის რეჟიმი","SIM Mobile Hotspot":"SIM ბარათის ინტერნეტით Hotspot","Wi-Fi Repeater":"Wi-Fi გამეორება","Select source Wi-Fi interface":"აირჩიე საწყისი Wi-Fi ინტერფეისი","Select interface":"აირჩიე ინტერფეისი","No data":"მონაცემები არ არის","Device capabilities":"მოწყობილობის შესაძლებლობები","Network interfaces":"ქსელის ინტერფეისები","Repeater readiness":"გამეორების რეჟიმის მზადყოფნა","Advanced settings and diagnostics":"დამატებითი პარამეტრები და დიაგნოსტიკა","Internet source (WAN)":"ინტერნეტის წყარო (WAN)","AP interface":"წვდომის წერტილის ინტერფეისი (AP)","Hotspot name":"Hotspot-ის სახელი","Password":"პაროლი","Scan Wi-Fi networks":"Wi-Fi ქსელების ძიება","Refresh data":"მონაცემების განახლება","Network control panel":"ქსელის მართვის პანელი","Administrator panel":"ადმინისტრატორის პანელი","System overview":"სისტემის მიმოხილვა","Network management":"ქსელის მართვა","Mobile network management":"მობილური ქსელის მართვა","Firewall & NAT":"Firewall და NAT","WAN / LAN & DHCP":"WAN / LAN და DHCP"});

const originalTextNodes = new WeakMap();
let uiLanguage = localStorage.getItem("keyland-language") === "en" ? "en" : "ka";
function translateTextNode(node) {
    if (!node || node.nodeType !== Node.TEXT_NODE) return;
    if (!originalTextNodes.has(node)) originalTextNodes.set(node, node.nodeValue);
    const original = originalTextNodes.get(node);
    const trimmed = original.trim();
    if (!trimmed) return;
    const translated = uiLanguage === "en" ? (translations[trimmed] || trimmed) : (englishToGeorgian[trimmed] || reverseTranslations[trimmed]);
    node.nodeValue = translated ? original.replace(trimmed, translated) : original;
}
function translateAddedContent(root) {
    if (!root) return;
    if (root.nodeType === Node.TEXT_NODE) { translateTextNode(root); return; }
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) translateTextNode(node);
}
function applyLanguage(language) {
    uiLanguage = language === "en" ? "en" : "ka";
    localStorage.setItem("keyland-language", uiLanguage);
    document.documentElement.lang = uiLanguage;
    translateAddedContent(document.body);
    const toggle = document.getElementById("languageToggle");
    if (toggle) toggle.textContent = uiLanguage === "en" ? "ქარ" : "EN";
    if (typeof renderNotifications === "function") renderNotifications();
}
function tr(ka, en) { return uiLanguage === "en" ? en : ka; }
document.getElementById("languageToggle").addEventListener("click", () => applyLanguage(uiLanguage === "en" ? "ka" : "en"));
applyLanguage(uiLanguage);
const languageObserver = new MutationObserver(records => {
    for (const record of records) for (const added of record.addedNodes) translateAddedContent(added);
});
languageObserver.observe(document.body, {childList:true,subtree:true});


function formatRate(mbps) {
    if (!Number.isFinite(mbps) || mbps < 0.01) return "< 0.01 Mbps";
    return mbps >= 100 ? mbps.toFixed(0) + " Mbps" : mbps.toFixed(2) + " Mbps";
}


let hotspotDnsCache = null;

const DNS_SERVICE_RULES = [
    {label:"WhatsApp", suffixes:["whatsapp.com","whatsapp.net"]},
    {label:"Facebook / Meta", suffixes:["facebook.com","facebook.net","fbcdn.net","messenger.com","instagram.com","cdninstagram.com"]},
    {label:"Google", suffixes:["google.com","googleapis.com","googleusercontent.com","gstatic.com","googlevideo.com","youtube.com","ytimg.com","ggpht.com"]},
    {label:"Apple", suffixes:["apple.com","icloud.com","apple-dns.net","mzstatic.com"]},
    {label:"TikTok", suffixes:["tiktok.com","tiktokv.com","tiktokcdn.com","byteoversea.com","ibytedtos.com"]},
    {label:"Microsoft", suffixes:["microsoft.com","microsoftonline.com","live.com","msftconnecttest.com","windows.com","office.com"]},
    {label:"Telegram", suffixes:["telegram.org","t.me","telegra.ph"]},
    {label:"Netflix", suffixes:["netflix.com","nflxvideo.net","nflximg.net"]},
    {label:"Cloudflare", suffixes:["cloudflare.com","cloudflare-dns.com"]},
    {label:"DNS / Connectivity", suffixes:["one.one.one.one","connectivitycheck.gstatic.com","captive.apple.com"] }
];

function dnsServiceFor(domain) {
    const host = String(domain || "").toLowerCase().replace(/\.$/, "");
    const rule = DNS_SERVICE_RULES.find(item =>
        item.suffixes.some(suffix => host === suffix || host.endsWith("." + suffix))
    );
    return rule ? rule.label : tr("სხვა / დაუჯგუფებელი", "Other / unclassified");
}

function renderHotspotDnsQueries(data) {
    hotspotDnsCache = data || null;
    const list = element("hotspotDnsQueries");
    const count = element("hotspotDnsCount");
    const search = element("deviceActivitySearch");
    if (!list || !count) return;

    const queries = Array.isArray(data?.queries) ? data.queries : [];
    count.textContent = queries.length + " " + tr("მოთხოვნა", "queries");

    if (!data?.enabled) {
        list.replaceChildren();
        const empty = document.createElement("div");
        empty.className = "client-empty";
        empty.textContent = data?.message || tr("DNS მონიტორინგი გამორთულია.", "DNS monitoring is disabled.");
        list.append(empty);
        return;
    }

    if (!queries.length) {
        list.replaceChildren();
        const empty = document.createElement("div");
        empty.className = "client-empty";
        empty.textContent = tr("მოთხოვნები ჯერ არ დაფიქსირებულა.", "No DNS requests recorded yet.");
        list.append(empty);
        return;
    }

    const term = (search?.value || "").trim().toLowerCase();
    const devices = new Map();

    for (const item of queries) {
        const identity = String(item.mac || item.ip || "unknown").toLowerCase();
        if (!devices.has(identity)) {
            devices.set(identity, {
                hostname:item.hostname || null, ip:item.ip || null, mac:item.mac || null,
                events:[], domains:new Map(), services:new Map()
            });
        }
        const device = devices.get(identity);
        if (!device.hostname && item.hostname) device.hostname = item.hostname;
        if (!device.ip && item.ip) device.ip = item.ip;
        if (!device.mac && item.mac) device.mac = item.mac;
        device.events.push(item);

        const domain = String(item.domain || "").toLowerCase();
        if (!domain) continue;
        const service = dnsServiceFor(domain);
        if (!device.domains.has(domain)) {
            device.domains.set(domain, {domain, count:0, last:item.timestamp || "", types:new Set(), service});
        }
        const record = device.domains.get(domain);
        record.count += 1;
        if (item.timestamp && (!record.last || String(item.timestamp).localeCompare(String(record.last)) > 0)) record.last = item.timestamp;
        if (item.query_type) record.types.add(String(item.query_type).toUpperCase());
        if (!device.services.has(service)) device.services.set(service, new Map());
        device.services.get(service).set(domain, record);
    }

    const matchingDevices = Array.from(devices.values()).filter(device => {
        if (!term) return true;
        const identity = [device.hostname,device.ip,device.mac].filter(Boolean).join(" ").toLowerCase();
        if (identity.includes(term)) return true;
        return Array.from(device.domains.keys()).some(domain => domain.includes(term) ||
            dnsServiceFor(domain).toLowerCase().includes(term));
    }).sort((a,b) => {
        const at = a.events[a.events.length - 1]?.timestamp || "";
        const bt = b.events[b.events.length - 1]?.timestamp || "";
        return String(bt).localeCompare(String(at));
    });

    if (!matchingDevices.length) {
        list.replaceChildren();
        const empty = document.createElement("div");
        empty.className = "client-empty";
        empty.textContent = tr("ამ ძებნით ჩანაწერი ვერ მოიძებნა.", "No activity matches this search.");
        list.append(empty);
        return;
    }

    const cards = matchingDevices.map(device => {
        const card = document.createElement("article");
        card.className = "client-device device-activity-group";

        const heading = document.createElement("div");
        heading.className = "device-activity-heading";
        const identity = document.createElement("div");
        const name = document.createElement("strong");
        name.className = "device-activity-name";
        name.textContent = device.hostname || tr("უცნობი მოწყობილობა", "Unknown device");
        const meta = document.createElement("div");
        meta.className = "muted";
        meta.textContent = [device.ip,device.mac].filter(Boolean).join(" · ") ||
            tr("მოწყობილობის იდენტიფიკაცია ვერ მოხერხდა", "Device identity unavailable");
        identity.append(name,meta);

        const stats = document.createElement("div");
        stats.className = "device-activity-stats";
        const total = document.createElement("span");
        total.textContent = device.events.length + " " + tr("მოთხოვნა", "queries");
        const unique = document.createElement("span");
        unique.textContent = device.domains.size + " " + tr("უნიკალური დომენი", "unique domains");
        stats.append(total,unique);
        heading.append(identity,stats);
        card.append(heading);

        const servicesTitle = document.createElement("h4");
        servicesTitle.className = "device-activity-section-title";
        servicesTitle.textContent = tr("აპლიკაციები და სერვისები", "Apps & services");
        card.append(servicesTitle);

        const services = Array.from(device.services.entries())
            .sort((a,b) => {
                const ac = Array.from(a[1].values()).reduce((n,x)=>n+x.count,0);
                const bc = Array.from(b[1].values()).reduce((n,x)=>n+x.count,0);
                return bc-ac;
            });

        const serviceList = document.createElement("div");
        serviceList.className = "device-activity-services";
        for (const [label,domainMap] of services) {
            const group = document.createElement("details");
            group.className = "device-activity-service";
            const summary = document.createElement("summary");
            const serviceName = document.createElement("strong");
            serviceName.textContent = label;
            const serviceMeta = document.createElement("span");
            const records = Array.from(domainMap.values());
            const requestTotal = records.reduce((n,x)=>n+x.count,0);
            serviceMeta.textContent = records.length + " " + tr("დომენი", "domains") + " · " + requestTotal + " " + tr("მოთხოვნა", "queries");
            summary.append(serviceName,serviceMeta);
            group.append(summary);

            const domainList = document.createElement("div");
            domainList.className = "device-activity-domain-list";
            records.sort((a,b)=>b.count-a.count || b.last.localeCompare(a.last));
            for (const record of records) {
                const row = document.createElement("div");
                row.className = "device-activity-domain";
                const domain = document.createElement("strong");
                domain.textContent = record.domain;
                const detail = document.createElement("small");
                detail.textContent = record.count + "× · " + (record.last || "—");
                row.append(domain,detail);
                domainList.append(row);
            }
            group.append(domainList);
            serviceList.append(group);
        }
        card.append(serviceList);

        const historyTitle = document.createElement("h4");
        historyTitle.className = "device-activity-section-title";
        historyTitle.textContent = tr("ბოლო მოთხოვნები", "Recent queries");
        card.append(historyTitle);
        const history = document.createElement("div");
        history.className = "device-activity-history";
        for (const item of device.events.slice().reverse().slice(0,8)) {
            const row = document.createElement("div");
            row.className = "device-activity-history-row";
            const domain = document.createElement("strong");
            domain.textContent = item.domain || "—";
            const detail = document.createElement("small");
            detail.textContent = item.timestamp || "—";
            row.append(domain,detail);
            history.append(row);
        }
        card.append(history);
        return card;
    });

    list.replaceChildren(...cards);
}

const deviceActivitySearch = element("deviceActivitySearch");
if (deviceActivitySearch) {
    deviceActivitySearch.addEventListener("input", () => {
        if (hotspotDnsCache) renderHotspotDnsQueries(hotspotDnsCache);
    });
}

let hotspotDnsPollingTimer = null;

async function refreshHotspotDnsActivity() {
    if (!state.authenticated) return;
    try {
        const data = await fetchJSON(API + "/wifi/hotspot/router/dns/queries?limit=200");
        if (state.authenticated) renderHotspotDnsQueries(data);
    } catch (error) {
        const count = element("hotspotDnsCount");
        if (count && !hotspotDnsCache) count.textContent = tr("API შეცდომა", "API error");
    }
}
function renderHotspotClients(status) {
    const list = element("hotspotClients");
    const count = element("hotspotClientCount");
    if (!list || !count) return;
    const clients = Array.isArray(status?.clients) ? status.clients : [];
    if (status?.active && status?.clients_available === false) {
        count.textContent = tr("მონაცემები მიუწვდომელია", "data unavailable");
        list.replaceChildren();
        const unavailable = document.createElement("div");
        unavailable.className = "client-empty";
        unavailable.textContent = status.clients_error
            ? tr("კლიენტების სიის წაკითხვა ვერ მოხერხდა: ", "Could not read client list: ") + status.clients_error
            : tr("Wi-Fi კლიენტების მონაცემები მიუწვდომელია.", "Wi-Fi client data is unavailable.");
        list.append(unavailable);
        hotspotClientSamples.clear();
        return;
    }

    count.textContent = clients.length + " " + tr("მოწყობილობა", "devices");

    if (!status?.active || clients.length === 0) {
        list.replaceChildren();
        const empty = document.createElement("div");
        empty.className = "client-empty";
        empty.textContent = !status?.active
            ? tr("Hotspot გამორთულია.", "Hotspot is stopped.")
            : tr("დაკავშირებული მოწყობილობები არ არის.", "No devices are connected.");
        list.append(empty);
        hotspotClientSamples.clear();
        return;
    }

    const now = Date.now();
    const cards = clients.map(client => {
        const mac = String(client.mac || "unknown");
        const rx = Number(client.rx_bytes) || 0;
        const tx = Number(client.tx_bytes) || 0;
        const previous = hotspotClientSamples.get(mac);
        const elapsed = previous ? Math.max(0.1, (now - previous.at) / 1000) : 0;
        const uploadMbps = previous && rx >= previous.rx ? ((rx - previous.rx) * 8 / elapsed) / 1000000 : 0;
        const downloadMbps = previous && tx >= previous.tx ? ((tx - previous.tx) * 8 / elapsed) / 1000000 : 0;
        hotspotClientSamples.set(mac, {rx, tx, at: now});

        const card = document.createElement("article");
        card.className = "client-device";
        const head = document.createElement("div");
        head.className = "client-device-head";
        const identity = document.createElement("strong");
        identity.textContent = client.hostname || client.ip || mac;
        const signal = document.createElement("span");
        signal.className = "client-signal";
        signal.textContent = String(client.signal || client.signal_avg || "—");
        head.append(identity, signal);

        const meta = document.createElement("div");
        meta.className = "muted";
        const radioLabel = client.ap_band
            ? client.ap_band + (client.ap_channel ? " · CH " + client.ap_channel : "") +
                (client.ap_frequency_mhz ? " · " + client.ap_frequency_mhz + " MHz" : "")
            : "Wi-Fi დიაპაზონი მიუწვდომელია";
        meta.textContent = mac + (client.ip ? " · " + client.ip : "") + " · " + radioLabel;

        const traffic = document.createElement("div");
        traffic.className = "client-traffic";
        const download = document.createElement("div");
        const downloadLabel = document.createElement("small");
        downloadLabel.textContent = tr("ჩამოტვირთული", "Downloaded");
        const downloadValue = document.createElement("b");
        downloadValue.textContent = formatBytes(tx);
        download.append(downloadLabel, downloadValue);
        const upload = document.createElement("div");
        const uploadLabel = document.createElement("small");
        uploadLabel.textContent = tr("ატვირთული", "Uploaded");
        const uploadValue = document.createElement("b");
        uploadValue.textContent = formatBytes(rx);
        upload.append(uploadLabel, uploadValue);
        traffic.append(download, upload);

        const connected = Number(client.connected_time) || 0;
        const duration = connected >= 3600
            ? Math.floor(connected / 3600) + "სთ " + Math.floor((connected % 3600) / 60) + "წთ"
            : Math.floor(connected / 60) + "წთ";
        const rate = document.createElement("div");
        rate.className = "client-rate";
        rate.textContent = "↓ " + formatRate(downloadMbps) + " · ↑ " + formatRate(uploadMbps) + " · " + duration;
        if (client.speed_limit) {
            const policy = document.createElement("div");
            policy.className = "muted";
            policy.textContent = "ლიმიტი: ↓ " + client.speed_limit.download_mbps + " Mbps · ↑ " + client.speed_limit.upload_mbps + " Mbps";
            rate.append(document.createElement("br"), policy);
        }

        const extra = document.createElement("details");
        extra.className = "diagnostic-details";
        const extraSummary = document.createElement("summary");
        extraSummary.textContent = "დეტალური ტექნიკური მონაცემები";
        const extraGrid = document.createElement("div");
        extraGrid.className = "technical-grid";
        const extraFields = [
            ["RX პაკეტები", client.rx_packets],
            ["TX პაკეტები", client.tx_packets],
            ["TX retries", client.tx_retries],
            ["TX failed", client.tx_failed],
            ["Inactive time", client.inactive_time != null ? client.inactive_time + " ms" : null],
            ["Wi-Fi დიაპაზონი", client.ap_band],
            ["Wi-Fi არხი", client.ap_channel],
            ["Wi-Fi სიხშირე", client.ap_frequency_mhz != null ? client.ap_frequency_mhz + " MHz" : null],
            ["TX bitrate", client.tx_bitrate],
            ["RX bitrate", client.rx_bitrate],
            ["სიგნალის ხარისხი", client.signal_quality_percent != null ? client.signal_quality_percent + "%" : null],
            ["Authorized", client.authorized],
            ["Authenticated", client.authenticated],
            ["Associated", client.associated],
            ["Client ID", client.client_id],
            ["Lease expiry", client.lease_expiry],
            ["Expected throughput", client.expected_throughput != null ? client.expected_throughput + " kbps" : null]
        ];
        extraFields.forEach(([label, value]) => {
            if (value === null || value === undefined || value === "") return;
            const field = document.createElement("div");
            field.className = "technical-field";
            const title = document.createElement("span");
            title.className = "technical-label";
            title.textContent = label;
            const content = document.createElement("strong");
            content.className = "technical-value";
            content.textContent = String(value);
            field.append(title, content);
            extraGrid.appendChild(field);
        });
        extra.append(extraSummary, extraGrid);

        const actions = document.createElement("div");
        actions.className = "client-device-actions";
        const block = document.createElement("button");
        block.className = client.blocked ? "secondary" : "danger";
        block.textContent = client.blocked ? tr("განბლოკვა", "Unblock") : tr("დაბლოკვა", "Block");
        block.addEventListener("click", () => manageHotspotClient(
            client, client.blocked ? "unblock" : "block"
        ));
        const limit = document.createElement("button");
        limit.className = "secondary";
        limit.textContent = tr("სიჩქარის ლიმიტი", "Speed limit");
        limit.addEventListener("click", () => manageHotspotClient(client, "limit"));
        actions.append(block, limit);
        card.append(head, meta, traffic, rate, extra, actions);
        return card;
    });
    list.replaceChildren(...cards);
}

async function manageHotspotClient(client, action) {
    const mac = String(client.mac || "");
    if (!mac) return;
    let endpoint = "";
    let payload = {mac, confirmed: true};

    if (action === "block" || action === "unblock") {
        const verb = action === "block" ? "დაბლოკო" : "განბლოკო";
        if (!window.confirm(mac + " — ნამდვილად გსურს მოწყობილობის " + verb + "?")) return;
        endpoint = API + "/wifi/hotspot/router/clients/" + action;
    } else {
        const download = window.prompt("ჩამოტვირთვის მაქსიმალური სიჩქარე Mbps-ში:", "10");
        if (download === null) return;
        const upload = window.prompt("ატვირთვის მაქსიმალური სიჩქარე Mbps-ში:", "5");
        if (upload === null) return;
        const down = Number(download), up = Number(upload);
        if (!Number.isInteger(down) || !Number.isInteger(up) || down < 1 || up < 1 || down > 1000 || up > 1000) {
            window.alert("მიუთითე მთელი რიცხვი 1-დან 1000 Mbps-მდე.");
            return;
        }
        if (!window.confirm(mac + " — ჩამოტვირთვა " + down + " Mbps, ატვირთვა " + up + " Mbps. გამოიყენო?")) return;
        endpoint = API + "/wifi/hotspot/router/clients/limit";
        payload.download_mbps = down;
        payload.upload_mbps = up;
    }

    try {
        const response = await apiFetch(endpoint, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(payload)
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || data.error || ("HTTP " + response.status));
        element("hotspotMessage").textContent = data.blocked === true
            ? "მოწყობილობა დაიბლოკა: " + mac
            : data.blocked === false
                ? "მოწყობილობა განიბლოკა: " + mac
                : "სიჩქარის ლიმიტი დაყენებულია: " + mac;
        if (action === "block" || action === "unblock") {
            const verbKa = action === "block" ? "დაიბლოკა" : "განიბლოკა";
            const verbEn = action === "block" ? "was blocked" : "was unblocked";
            addNotification("info", "ადმინისტრატორის მოქმედება", "Administrator action", mac + " — მოწყობილობა " + verbKa + ".", mac + " — device " + verbEn + ".");
        } else {
            addNotification("info", "სიჩქარის ლიმიტი შეიცვალა", "Speed limit updated", mac + " · ჩამოტვირთვა " + payload.download_mbps + " Mbps · ატვირთვა " + payload.upload_mbps + " Mbps", mac + " · download " + payload.download_mbps + " Mbps · upload " + payload.upload_mbps + " Mbps");
        }
        await loadHotspotCapabilities();
    } catch (error) {
        element("hotspotMessage").textContent = "მოქმედება ვერ შესრულდა: " + error.message;
    }
}


/* Hotspot preflight and guarded controls */
async function loadHotspotCapabilities() {
    const output = element("hotspotPreflight");
    const select = element("hotspotInterface");
    const start = element("hotspotStartButton");
    output.textContent = tr("ქსელის შესაძლებლობები მოწმდება...","Checking network capabilities...");
    start.disabled = true;
    try {
        const data = await fetchJSON(API + "/wifi/hotspot/capabilities");
const source = element("hotspotSourceInterface");
const previousSource = source.value;
source.replaceChildren(new Option(
    tr("აირჩიე საწყისი Wi-Fi ინტერფეისი", "Select source Wi-Fi interface"), ""
));
(data.interfaces || []).filter(item => item.type === "managed").forEach(item => {
    source.add(new Option(
        item.name + (item.ssid ? " · connected" : " · managed"),
        item.name
    ));
});
source.value = (data.interfaces || []).some(
    item => item.name === previousSource && item.type === "managed"
) ? previousSource : (
    (data.interfaces || []).some(
        item => item.name === "wlan0" && item.type === "managed"
    ) ? "wlan0" : ""
);

        select.replaceChildren(new Option("keyland-ap · dedicated AP", "keyland-ap"));
        (data.ap_candidates || []).filter(item => item.type === "AP" && item.name !== "wlan0").forEach(item => {
            if (item.name !== "keyland-ap") select.add(new Option(item.name + " · AP", item.name));
        });
        const wan = element("hotspotUpstream");
        wan.replaceChildren(new Option(tr("აირჩიე WAN ინტერფეისი","Select WAN interface"), ""));
        (data.upstream_sources || []).forEach(item => {
            wan.add(new Option(item.interface + " · " + item.type, item.interface));
        });
        const selectedMode = element("hotspotMode")?.value || "hotspot";
        const selectedSource = (data.interfaces || []).find(
            x => x.name === source.value && x.type === "managed"
        );

        const wifiWan = (data.upstream_sources || []).find(
            x => x.type === "wifi" && x.interface === source.value
        );
        const simWan = (data.upstream_sources || []).find(
            x => x.type === "cellular" && x.interface === "qmapmux0.0"
        ) || (data.upstream_sources || []).find(x => x.type === "cellular");

        const preferredWan = selectedMode === "repeater" ? wifiWan : simWan;
        wan.value = preferredWan?.interface || "";

        const station = selectedMode === "repeater"
            ? (selectedSource?.ssid ? selectedSource : null)
            : (data.interfaces || []).find(
                x => x.type === "managed" && x.ssid && Number(x.channel)
            );

        const stationChannel = Number(station?.channel || 6);
        const stationBand = stationChannel >= 36 ? "5" : "2.4";

        element("hotspotBand").value = stationBand;
        updateHotspotChannels();

        if ((stationBand === "2.4" && stationChannel >= 1 && stationChannel <= 11) ||
            (stationBand === "5" && [36,40,44,48].includes(stationChannel))) {
            element("hotspotChannel").value = String(stationChannel);
        }

        // Restore the last successfully applied configuration from the protected API.
        const savedResult = await fetchJSON(API + "/wifi/hotspot/router/settings").catch(() => ({saved:false}));
        const saved = savedResult && savedResult.saved && savedResult.settings
            ? savedResult.settings : null;
        const savedState = element("hotspotSavedState");
        const showSaved = (id, value) => {
            const node = element(id);
            if (node) node.textContent = value == null || value === "" ? "—" : String(value);
        };
        if (saved) {
            const fields = {
                hotspotMode: saved.mode,
                hotspotInterface: saved.interface,
                hotspotSourceInterface: saved.source_interface,
                hotspotUpstream: saved.upstream_interface,
                hotspotSsid: saved.ssid,
                hotspotPassword: saved.password,
                hotspotDns: Array.isArray(saved.dns_servers) ? saved.dns_servers.join(", ") : "",
                hotspotIpv4: saved.ipv4_cidr,
                hotspotBand: saved.band,
                hotspotChannel: saved.channel,
                hotspotDnsMonitoring: saved.dns_monitoring
            };
            Object.entries(fields).forEach(([id, value]) => {
                const field = element(id);
                if (!field) return;
                if (field.type === "checkbox") field.checked = value === true;
                else if (value != null && value !== "") field.value = String(value);
            });
            updateHotspotChannels();
            if (saved.channel != null) element("hotspotChannel").value = String(saved.channel);
            showSaved("savedHotspotSsid", saved.ssid);
            showSaved("savedHotspotPassword", saved.password);
            showSaved("savedHotspotBand", saved.band ? saved.band + " GHz" : "—");
            showSaved("savedHotspotChannel", saved.channel);
            showSaved("savedHotspotWan", saved.upstream_interface);
            showSaved("savedHotspotGateway", saved.ipv4_cidr);
            if (savedState) savedState.textContent = "შენახულია · ბოლო წარმატებული გაშვება";
        } else {
            ["savedHotspotSsid","savedHotspotPassword","savedHotspotBand","savedHotspotChannel","savedHotspotWan","savedHotspotGateway"].forEach(id => showSaved(id, "—"));
            if (savedState) savedState.textContent = "შენახული კონფიგურაცია ჯერ არ არის";
        }

        if (selectedMode === "repeater" && !wifiWan) {
            element("hotspotMessage").textContent = tr(
                "არჩეული Wi-Fi ინტერფეისი დაკავშირებული არ არის.",
                "The selected Wi-Fi interface is not connected."
            );
        }
        element("hotspotCreateInterfaceButton").disabled = !(data.ap_mode_supported && (data.interfaces || []).some(x => x.type === "managed"));
        const status = await fetchJSON(API + "/wifi/hotspot/router/status").catch(() => ({active:false}));

        // Reflect the real running router configuration in the dashboard.
        // The status API deliberately does not expose the Wi-Fi password.
        const activeConfig = status && status.active && status.configuration
            ? status.configuration : null;

        if (activeConfig) {
            const setField = (id, value) => {
                const field = element(id);
                if (!field || value == null || value === "") return;
                field.value = String(value);
            };

            setField("hotspotMode", activeConfig.mode);
            setField("hotspotInterface", activeConfig.interface);
            setField("hotspotSourceInterface", activeConfig.source_interface);
            setField("hotspotUpstream", activeConfig.upstream_interface);
            setField("hotspotSsid", activeConfig.ssid);
            setField("hotspotIpv4", activeConfig.ipv4_cidr);
            setField("hotspotBand", activeConfig.band);

            if (Array.isArray(activeConfig.dns_servers)) {
                setField("hotspotDns", activeConfig.dns_servers.join(", "));
            }

            const dnsMonitoring = element("hotspotDnsMonitoring");
            if (dnsMonitoring && typeof activeConfig.dns_monitoring === "boolean") {
                dnsMonitoring.checked = activeConfig.dns_monitoring;
            }

            updateHotspotChannels();
            if (activeConfig.channel != null) {
                setField("hotspotChannel", activeConfig.channel);
            }

            // Do not populate the password field from runtime status.
            showSaved("savedHotspotSsid", activeConfig.ssid);
            showSaved("savedHotspotPassword", "დაცულია · პაროლი არ იკითხება");
            showSaved(
                "savedHotspotBand",
                activeConfig.band ? activeConfig.band + " GHz" : "—"
            );
            showSaved("savedHotspotChannel", activeConfig.channel);
            showSaved("savedHotspotWan", activeConfig.upstream_interface);
            showSaved(
                "savedHotspotGateway",
                activeConfig.gateway || activeConfig.ipv4_cidr
            );

            if (savedState) {
                savedState.textContent = "აქტიურია · მიმდინარე კონფიგურაცია";
            }
        }

        const autostart = await fetchJSON(API + "/wifi/hotspot/router/autostart").catch(() => ({enabled:false}));
        const clientData = await fetchJSON(API + "/wifi/hotspot/router/clients").catch(() => ({active:false,clients:[]}));
        renderHotspotClients(clientData);
        start.disabled = !wan.value || status.configured;
        element("hotspotStopButton").disabled = !status.configured;
        element("hotspotAutostartDisableButton").disabled = !autostart.enabled;
        element("hotspotAutostartEnableButton").disabled = autostart.enabled || !status.active;
        showJSON("hotspotPreflight", {
            ap_mode_supported: data.ap_mode_supported,
            interfaces: data.interfaces || [],
            ap_candidates: data.ap_candidates || [],
            upstream_sources: data.upstream_sources || [],
            default_route_interfaces: data.default_route_interfaces || [],
            router_status: {
                active: status.active,
                configured: status.configured,
                clients: status.clients || [],
                configuration: status.configuration || {}
            },
            automatic_start: {
                enabled: autostart.enabled,
                active: autostart.active,
                upstream_interface: autostart.upstream_interface,
                message: autostart.message
            },
            manager: "hostapd + dnsmasq + nftables",
            limitations: data.limitations || []
        });
        element("hotspotMessage").textContent = status.active
            ? tr("როუტერი ჩართულია. კონფიგურაცია ავტომატურად აღდგება გადატვირთვის შემდეგ.","Router is active. Configuration will be restored automatically after reboot.")
            : autostart.enabled
                ? tr("კონფიგურაცია შენახულია. ველოდები WAN/Wi-Fi კავშირის მზადყოფნას.","Configuration is saved. Waiting for WAN/Wi-Fi readiness.")
                : status.configured
                    ? tr("წინა გაშვება არასრულად დასრულდა. ჯერ გამორთე/გაწმინდე კონფიგურაცია.","Previous startup was incomplete. Stop and clean it before restarting.")
                    : tr("აირჩიე WAN და AP ინტერფეისი, შემდეგ ჩართე.","Choose WAN and AP interface, then start.");
    } catch (error) {
        showJSON("hotspotPreflight", { error: tr("შემოწმება ვერ მოხერხდა: ","Preflight failed: ") + error.message });
    }
}
async function startHotspot() {
    const mode = element("hotspotMode").value;
    const interfaceName = element("hotspotInterface").value || "keyland-ap";
    const upstream = element("hotspotUpstream").value;
    const sourceInterface = element("hotspotSourceInterface").value || "wlan0";
    const ssid = element("hotspotSsid").value.trim();
    const password = element("hotspotPassword").value;
    const dnsServers = element("hotspotDns").value.split(",").map(v => v.trim()).filter(Boolean);
    const ipv4Cidr = element("hotspotIpv4").value.trim();
    const band = element("hotspotBand").value;
    const channel = Number(element("hotspotChannel").value);

    if (!upstream) {
        element("hotspotMessage").textContent = tr("აირჩიე ინტერნეტის წყარო.","Select an upstream interface.");
        return;
    }

    if (mode === "repeater" && upstream !== sourceInterface) {
        element("hotspotMessage").textContent = tr(
            "Repeater რეჟიმში WAN უნდა იყოს საწყისი Wi-Fi ინტერფეისი.",
            "In repeater mode, WAN must be the source Wi-Fi interface."
        );
        return;
    }

    if (!password || password.length < 8 || password.length > 63 || !/^[\x20-\x7E]+$/.test(password)) {
        element("hotspotMessage").textContent = tr("WPA2 პაროლი უნდა იყოს 8–63 ინგლისური/ASCII სიმბოლო.","WPA2 password must be 8–63 ASCII characters.");
        return;
    }

    if (!window.confirm(tr(
        "ჯერ მომზადდება ცალკე AP ინტერფეისი, შემდეგ გაეშვება DHCP და NAT. არსებული WAN კავშირი არ გადაირთვება. გავაგრძელოთ?",
        "The dedicated AP interface will be prepared, then DHCP and NAT will start. The existing WAN connection will not be switched. Continue?"
    ))) return;

    const button = element("hotspotStartButton");
    button.disabled = true;

    try {
        // Prepare AP on every start, including after stop or rollback.
        const prepareResponse = await apiFetch(API + "/wifi/hotspot/create-interface", {
            method: "POST",
            headers: {"Content-Type":"application/json"},
            body: JSON.stringify({
                interface: sourceInterface,
                ap_interface: interfaceName,
                confirmed: true
            })
        });

        const prepared = await prepareResponse.json().catch(() => ({}));
        if (!prepareResponse.ok || prepared.success === false) {
            throw new Error(prepared.detail || prepared.error || "AP interface preparation failed.");
        }

        const response = await apiFetch(API + "/wifi/hotspot/router/start", {
            method: "POST",
            headers: {"Content-Type":"application/json"},
            body: JSON.stringify({
                mode,
                interface: interfaceName,
                source_interface: sourceInterface,
                upstream_interface: upstream,
                ssid,
                password,
                dns_servers: dnsServers,
                ipv4_cidr: ipv4Cidr,
                band,
                channel,
                dns_monitoring: element("hotspotDnsMonitoring").checked,
                confirmed: true
            })
        });

        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || data.error || ("HTTP " + response.status));

        element("hotspotMessage").textContent =
            tr("როუტერი ჩაირთო და კონფიგურაცია შენახულია: ", "Router started; configuration saved for reboot: ") +
            data.ssid + " · WAN " + data.upstream_interface + " · " + data.ipv4_cidr;

        await loadHotspotCapabilities();
    } catch(error) {
        element("hotspotMessage").textContent =
            tr("ჩართვა ვერ მოხერხდა: ", "Start failed: ") + error.message;
    } finally {
        button.disabled = false;
    }
}
async function enableHotspotAutostart() {
    if (!window.confirm(tr(
        "ჩაირთოს ავტომატური გაშვება? მიმდინარე Router კონფიგურაცია უსაფრთხოდ შეინახება.",
        "Enable automatic startup? The current Router configuration will be saved securely."
    ))) return;
    const button = element("hotspotAutostartEnableButton");
    button.disabled = true;
    try {
        const response = await apiFetch(API + "/wifi/hotspot/router/autostart/enable", {
            method: "POST",
            headers: {"Content-Type":"application/json"},
            body: JSON.stringify({confirmed:true})
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || data.error || ("HTTP " + response.status));
        element("hotspotMessage").textContent = tr(
            "ავტომატური გაშვება ჩართულია.",
            "Automatic startup enabled."
        );
        await loadHotspotCapabilities();
    } catch (error) {
        element("hotspotMessage").textContent = tr("ვერ მოხერხდა: ", "Failed: ") + error.message;
    } finally {
        button.disabled = false;
    }
}

async function disableHotspotAutostart() {
    if (!window.confirm(tr(
        "გამორთო ავტომატური გაშვება? მიმდინარე Hotspot მუშაობას გააგრძელებს.",
        "Disable automatic startup? The currently running Hotspot will stay active."
    ))) return;
    const button = element("hotspotAutostartDisableButton");
    button.disabled = true;
    try {
        const response = await apiFetch(API + "/wifi/hotspot/router/autostart/disable", {
            method: "POST",
            headers: {"Content-Type":"application/json"},
            body: JSON.stringify({confirmed:true})
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || data.error || ("HTTP " + response.status));
        element("hotspotMessage").textContent = tr(
            "ავტომატური გაშვება გამორთულია. მიმდინარე Hotspot არ გათიშულა.",
            "Automatic startup disabled. The current Hotspot remains active."
        );
        await loadHotspotCapabilities();
    } catch (error) {
        element("hotspotMessage").textContent = tr("ვერ მოხერხდა: ", "Failed: ") + error.message;
    } finally {
        button.disabled = false;
    }
}

async function stopHotspot() {
    if (!window.confirm(tr("გამორთო Keyland Router Wi-Fi?","Stop Keyland Router Wi-Fi?"))) return;
    const button = element("hotspotStopButton"); button.disabled = true;
    try {
        const response = await apiFetch(API + "/wifi/hotspot/router/stop", {
            method:"POST", headers:{"Content-Type":"application/json"},
            body:JSON.stringify({confirmed:true})
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || ("HTTP " + response.status));
        element("hotspotMessage").textContent = tr("Hotspot გამორთულია.","Router Wi-Fi stopped.");
        await loadHotspotCapabilities();
    } catch(error) { element("hotspotMessage").textContent = tr("გამორთვა ვერ მოხერხდა: ","Stop failed: ") + error.message; }
    finally { button.disabled = false; }
}
async function createVirtualApInterface() {
    const interfaceName = element("hotspotSourceInterface").value;
    if (!interfaceName) {
        element("hotspotMessage").textContent = tr("აირჩიე საწყისი Wi-Fi ინტერფეისი.","Select the source Wi-Fi interface.");
        return;
    }
    if (!window.confirm(tr("შეიქმნება ცალკე keyland-ap ინტერფეისი. აქტიური Wi-Fi კავშირი არ უნდა გაითიშოს, მაგრამ დრაივერის შეზღუდვის შემთხვევაში ოპერაცია შეიძლება უარყოფილი იყოს. გავაგრძელოთ?","Create a separate keyland-ap interface. The active Wi-Fi connection will not be intentionally disconnected; the driver may reject this if concurrency is unsupported. Continue?"))) return;
    const button = element("hotspotCreateInterfaceButton");
    button.disabled = true;
    try {
        const response = await apiFetch(API + "/wifi/hotspot/create-interface", {
            method:"POST", headers:{"Content-Type":"application/json"},
            body:JSON.stringify({interface:interfaceName,ap_interface:"keyland-ap",confirmed:true})
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || ("HTTP " + response.status));
        element("hotspotMessage").textContent = data.message || tr("AP ინტერფეისი შეიქმნა.","AP interface created.");
        await loadHotspotCapabilities();
    } catch(error) {
        element("hotspotMessage").textContent = tr("AP ინტერფეისის შექმნა ვერ მოხერხდა: ","Could not create AP interface: ") + error.message;
    } finally { button.disabled = false; }
}
element("hotspotMode").addEventListener("change", async () => {
    const mode = element("hotspotMode").value;

    element("hotspotMessage").textContent = mode === "repeater"
        ? tr(
            "მოწმდება არჩეული Wi-Fi კავშირი და მისი არხი...",
            "Checking the selected Wi-Fi connection and channel..."
        )
        : tr(
            "მოწმდება SIM ინტერნეტ კავშირი...",
            "Checking the SIM internet connection..."
        );

    await loadHotspotCapabilities();
});

element("hotspotCreateInterfaceButton").addEventListener("click", createVirtualApInterface);
element("hotspotCheckButton").addEventListener("click", loadHotspotCapabilities);
element("hotspotStartButton").addEventListener("click", startHotspot);
element("hotspotStopButton").addEventListener("click", stopHotspot);
element("hotspotAutostartDisableButton").addEventListener("click", disableHotspotAutostart);
element("hotspotAutostartEnableButton").addEventListener("click", enableHotspotAutostart);
function updateHotspotChannels() {
    const band = element("hotspotBand").value;
    const channels = band === "5" ? [36, 40, 44, 48] : [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11];
    const select = element("hotspotChannel");
    const previous = Number(select.value);
    select.replaceChildren(...channels.map(channel => {
        const option = document.createElement("option");
        option.value = String(channel);
        option.textContent = String(channel);
        return option;
    }));
    select.value = String(channels.includes(previous) ? previous : (band === "5" ? 36 : 6));
}
element("hotspotBand").addEventListener("change", updateHotspotChannels);
updateHotspotChannels();

/* DNS MANAGEMENT */
function renderDnsStatus(data) {
    showJSON("dnsStatus", data);
}

async function loadDnsStatus() {
    if (!state.authenticated) return;
    const box = element("dnsStatus");
    showJSON("dnsStatus", { status: "იტვირთება..." });
    try {
        const response = await apiFetch(API + "/network/dns");
        const data = await response.json().catch(() => ({}));
        if (response.status === 404) {
            let version = "უცნობია";
            try {
                const health = await fetchJSON(API + "/health");
                version = health.version || version;
            } catch (_) {}
            throw new Error(tr(
                "DNS API ამ გაშვებულ სერვერზე არ არის რეგისტრირებული (HTTP 404). სერვერის ვერსია: " + version + ". საჭიროა Keyland Network v1.0.0 ან უფრო ახალი backend და სწორი systemd სერვისის განახლება.",
                "The running server does not expose the DNS endpoint (HTTP 404). Server version: " + version + ". Deploy the Keyland Network v1.0.0+ backend and restart the correct systemd service."
            ));
        }
        if (!response.ok) throw new Error(data.detail || ("HTTP " + response.status));
        renderDnsStatus(data);
        element("dnsMessage").textContent = "მდგომარეობა განახლებულია.";
    } catch (error) {
        showJSON("dnsStatus", { error: "DNS-ის წაკითხვა ვერ მოხერხდა.", details: error.message });
        element("dnsMessage").textContent = "შეამოწმეთ სერვერის ვერსია და API კავშირი.";
    }
}

async function submitDns(path, payload, confirmationText) {
    if (!state.authenticated) return;
    if (!window.confirm(confirmationText)) return;
    const buttons = ["dnsApplyButton", "dnsResetButton", "dnsRefreshButton"];
    buttons.forEach(id => element(id).disabled = true);
    try {
        const response = await apiFetch(API + path, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ...payload, confirmed: true })
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || result.error || ("HTTP " + response.status));
        element("dnsMessage").textContent = tr("მოთხოვნა შესრულდა. შედეგი გადაამოწმეთ ქვემოთ.","Request applied. Verify the result below.");
        await loadDnsStatus();
    } catch (error) {
        element("dnsMessage").textContent = tr("შეცდომა: ","Error: ") + error.message;
    } finally {
        buttons.forEach(id => element(id).disabled = false);
    }
}

element("dnsApplyButton").addEventListener("click", () => {
    const iface = element("dnsInterface").value.trim();
    const servers = element("dnsServers").value.split(",").map(v => v.trim()).filter(Boolean);
    if (!iface || !servers.length) {
        element("dnsMessage").textContent = "შეავსეთ ინტერფეისი და DNS სერვერები.";
        return;
    }
    submitDns("/network/dns", { interface: iface, servers }, "არჩეულ ინტერფეისზე დროებითი DNS პარამეტრები შეიცვლება. გავაგრძელოთ?");
});

element("dnsResetButton").addEventListener("click", () => {
    const iface = element("dnsInterface").value.trim();
    if (!iface) {
        element("dnsMessage").textContent = "მიუთითეთ ინტერფეისი.";
        return;
    }
    submitDns("/network/dns/reset", { interface: iface }, "არჩეულ ინტერფეისზე DNS ავტომატურ რეჟიმზე დაბრუნდება. გავაგრძელოთ?");
});

element("dnsRefreshButton").addEventListener("click", loadDnsStatus);

/* WLAN0 WAN IPv4 */
function toggleWlan0StaticFields() {
    const mode = element("wlan0WanMode")?.value;
    const fields = element("wlan0StaticFields");
    if (fields) fields.hidden = mode !== "static";
}

async function loadWlan0Wan() {
    if (!state.authenticated) return;
    const message = element("wlan0WanMessage");
    try {
        const data = await fetchJSON(API + "/network/wan/wlan0");
        element("wlan0WanMode").value = data.mode || "dhcp";
        const saved = data.saved || {};
        const address = String(saved.address || "").split("/");
        element("wlan0Address").value = address[0] || "";
        element("wlan0Prefix").value = address[1] || "24";
        element("wlan0Gateway").value = saved.gateway || "";
        element("wlan0Dns").value = (saved.dns || []).join(", ");
        toggleWlan0StaticFields();
        const active = data.active || {};
        const ip = (active.addresses || [])[0] || "—";
        const dns = (active.dns || []).join(", ") || "ავტომატური / უცნობია";
        element("wlan0WanCurrent").textContent =
            "პროფილი: " + data.profile + " · რეჟიმი: " + (data.mode === "static" ? "Static" : "DHCP") +
            " · აქტიური IP: " + ip + " · Gateway: " + (active.gateway || "—") + " · DNS: " + dns;
        if (message) message.textContent = "";
    } catch (error) {
        if (message) message.textContent = "WAN პარამეტრების წაკითხვა ვერ მოხერხდა: " + error.message;
    }
}

async function applyWlan0Wan() {
    const mode = element("wlan0WanMode").value;
    const payload = {mode, confirmed: true};
    if (mode === "static") {
        payload.address = element("wlan0Address").value.trim();
        payload.prefix = Number(element("wlan0Prefix").value);
        payload.gateway = element("wlan0Gateway").value.trim();
        payload.dns = element("wlan0Dns").value.split(",").map(value => value.trim()).filter(Boolean);
        if (!payload.address || !payload.gateway || !Number.isInteger(payload.prefix) || payload.prefix < 1 || payload.prefix > 32) {
            element("wlan0WanMessage").textContent = "შეავსე IP, Prefix და Gateway სწორად.";
            return;
        }
    }
    const warning = mode === "dhcp"
        ? "wlan0 გადავა DHCP/Automatic რეჟიმზე. MAGTI Wi-Fi-ის IP და მარშრუტი განახლდება. გავაგრძელოთ?"
        : "wlan0-ის IPv4 პარამეტრები შეიცვლება. არასწორმა IP/Gateway-მა შეიძლება ინტერნეტი გათიშოს. გავაგრძელოთ?";
    if (!window.confirm(warning)) return;
    const button = element("wlan0WanApply");
    button.disabled = true;
    element("wlan0WanMessage").textContent = "NetworkManager-ში პარამეტრები გამოიყენება...";
    try {
        const response = await apiFetch(API + "/network/wan/wlan0", {
            method: "PUT",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(payload)
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || ("HTTP " + response.status));
        element("wlan0WanMessage").textContent = data.message || "პარამეტრები განახლდა.";
        await loadWlan0Wan();
        await loadData();
    } catch (error) {
        element("wlan0WanMessage").textContent = "WAN-ის შეცვლა ვერ მოხერხდა: " + error.message;
    } finally {
        button.disabled = false;
    }
}

element("wlan0WanMode")?.addEventListener("change", toggleWlan0StaticFields);
element("wlan0WanRefresh")?.addEventListener("click", loadWlan0Wan);
element("wlan0WanApply")?.addEventListener("click", applyWlan0Wan);

/* WI-FI SCAN */
element("wifiScanFilter")?.addEventListener("input", () => {
    if (window.lastWifiScanData) renderWifiScan("wifiScan", window.lastWifiScanData);
});

function renderWifiScan(targetId, data) {
    const target = element(targetId);
    if (!target) return;
    target.replaceChildren();

    const allNetworks = Array.isArray(data.networks) ? data.networks : [];
    const selectedInterface = element("repeaterInterface")?.value || "";
    const interfaceNetworks = targetId === "wifiScan" && selectedInterface
        ? allNetworks.filter(item => item.interface === selectedInterface)
        : allNetworks;
    const filterText = targetId === "wifiScan"
        ? (element("wifiScanFilter")?.value || "").trim().toLowerCase()
        : "";
    const networks = filterText
        ? interfaceNetworks.filter(item =>
            String(item.ssid || "").toLowerCase().includes(filterText) ||
            String(item.bssid || "").toLowerCase().includes(filterText) ||
            String(item.band || "").toLowerCase().includes(filterText) ||
            String(item.frequency_mhz || "").includes(filterText))
        : interfaceNetworks;
    if (!networks.length) {
        const empty = document.createElement("p");
        empty.className = "muted";
        empty.textContent = filterText
            ? "ამ საძიებო სიტყვით ქსელი ვერ მოიძებნა."
            : (data.error || "ქსელები ვერ მოიძებნა. შეამოწმე Wi-Fi ინტერფეისი, rfkill და უფლებები.");
        target.appendChild(empty);
        if (Array.isArray(data.interfaces) && data.interfaces.length) {
            const detail = document.createElement("pre");
            detail.textContent = JSON.stringify(data.interfaces, null, 2);
            target.appendChild(detail);
        }
        return;
    }

    const grid = document.createElement("div");
    grid.className = "wifi-results-grid";
    networks.forEach(network => {
        const card = document.createElement("article");
        card.className = "wifi-result-card";
        const heading = document.createElement("div");
        heading.className = "wifi-result-heading";
        const name = document.createElement("strong");
        name.textContent = network.ssid || "(დამალული ქსელი)";
        const security = document.createElement("span");
        security.className = "wifi-security";
        security.textContent = network.security || "უცნობი დაცვა";
        heading.append(name, security);
        const meta = document.createElement("p");
        meta.className = "muted";
        const measuredDbm = network.signal_dbm != null ? network.signal_dbm : null;
        const estimatedDbm = network.signal_dbm_estimate != null ? network.signal_dbm_estimate : null;
        const signalParts = [];
        if (network.signal_percent != null) signalParts.push(network.signal_percent + "%");
        if (measuredDbm != null) signalParts.push(measuredDbm + " dBm");
        else if (estimatedDbm != null) signalParts.push("≈" + estimatedDbm + " dBm");
        const distance = network.distance_m_estimate != null
            ? "≈ " + network.distance_m_estimate + " მ"
            : "მანძილი —";
        const radioBand = network.band || (
            network.frequency_mhz >= 5925 ? "6 GHz" :
            network.frequency_mhz >= 4900 ? "5 GHz" :
            network.frequency_mhz >= 2400 ? "2.4 GHz" : ""
        );
        meta.textContent = [
            network.bssid || "",
            radioBand ? "დიაპაზონი " + radioBand : "სიხშირე უცნობია",
            network.channel ? "არხი " + network.channel : "",
            network.frequency_mhz ? network.frequency_mhz + " MHz" : "",
            "სიგნალი " + (signalParts.join(" / ") || "—"),
            distance
        ].filter(Boolean).join(" · ");
        const connect = document.createElement("button");
        connect.type = "button";
        connect.className = "secondary repeater-connect-button";
        connect.textContent = tr("დაკავშირება", "Connect");
        connect.addEventListener("click", () => connectRepeaterNetwork(network));
        card.append(heading, meta, connect);
        grid.appendChild(card);
    });
    target.appendChild(grid);

    const footer = document.createElement("p");
    footer.className = "muted";
    footer.textContent = "ნაპოვნია " + networks.length + " ქსელი · " +
        ((data.interfaces || []).map(item => item.interface + ": " + item.count).join(" / ") || "Wi-Fi scan");
    target.appendChild(footer);
}

async function scanWifi() {
    if (!state.authenticated || state.scanLoading) return;
    state.scanLoading = true;

    const button = element("scanButton");
    if (button) button.disabled = true;
    const target = element("wifiScan");
    if (target) target.textContent = "Wi-Fi ქსელების ძიება მიმდინარეობს...";

    try {
        const data = await fetchJSON(API + "/wifi/scan");
        window.lastWifiScanData = data;
        if (state.authenticated) renderWifiScan("wifiScan", data);
    } catch (error) {
        if (state.authenticated && target) target.textContent = "სკანირების შეცდომა: " + error.message;
    } finally {
        state.scanLoading = false;
        if (button) button.disabled = false;
    }
}

async function connectRepeaterNetwork(network) {
    const interfaceName = element("repeaterInterface").value || network.interface || "wlan0";
    const ssid = String(network.ssid || "");
    const bssid = String(network.bssid || "");
    const security = String(network.security || "Open").toLowerCase();
    const password = element("repeaterPassword").value;
    const secured = security !== "open" && security !== "--" && security !== "";
    if (secured && (password.length < 8 || password.length > 63)) {
        element("repeaterMessage").textContent = tr("შეიყვანე ქსელის სწორი პაროლი (8–63 სიმბოლო).", "Enter the network password (8–63 characters).");
        element("repeaterPassword").focus();
        return;
    }
    if (!window.confirm(tr(
        "Wi-Fi ადაპტერი დაუკავშირდება " + ssid + "-ს. მიმდინარე Wi-Fi ინტერნეტი შეიძლება დროებით გაითიშოს. გავაგრძელოთ?",
        "The Wi-Fi adapter will switch to " + ssid + ". Your current Wi-Fi internet connection may be interrupted. Continue?"
    ))) return;
    element("repeaterMessage").textContent = tr("ქსელთან დაკავშირება მიმდინარეობს...", "Connecting to Wi-Fi...");
    try {
        const response = await apiFetch(API + "/wifi/hotspot/repeater/connect", {
            method: "POST", headers: {"Content-Type":"application/json"},
            body: JSON.stringify({interface:interfaceName, ssid, bssid, password:secured ? password : "", confirmed:true})
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || data.error || ("HTTP " + response.status));
        element("repeaterPassword").value = "";
        element("repeaterMessage").textContent = tr("დაკავშირებულია: ", "Connected: ") + (data.ssid || ssid) + ". " + tr("ახლა შეგიძლია გააზიარო Hotspot-ით.", "You can now share it through Hotspot.");
        await loadData();
        await loadHotspotCapabilities();
    } catch (error) {
        element("repeaterMessage").textContent = tr("დაკავშირება ვერ მოხერხდა: ", "Connection failed: ") + error.message;
    }
}

async function disconnectRepeater() {
    const connection = repeaterCurrentConnection;
    if (!connection) return;
    if (!window.confirm(tr("გათიშო " + connection.ssid + "? ამ Wi-Fi კავშირის ინტერნეტი შეწყდება.", "Disconnect from " + connection.ssid + "? Internet through this Wi-Fi will stop."))) return;
    const button = element("repeaterDisconnectButton");
    button.disabled = true;
    try {
        const response = await apiFetch(API + "/wifi/hotspot/repeater/disconnect", {
            method:"POST", headers:{"Content-Type":"application/json"},
            body:JSON.stringify({interface:connection.name, confirmed:true})
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || data.error || ("HTTP " + response.status));
        element("repeaterMessage").textContent = tr("Wi-Fi კავშირი გათიშულია.", "Wi-Fi disconnected.");
        await loadData();
        await loadHotspotCapabilities();
    } catch (error) {
        element("repeaterMessage").textContent = tr("გათიშვა ვერ მოხერხდა: ", "Disconnect failed: ") + error.message;
    } finally {
        button.disabled = false;
    }
}

async function shareRepeaterConnection() {
    if (!repeaterCurrentConnection) return;
    element("hotspotMode").value = "repeater";
    element("hotspotSourceInterface").value = repeaterCurrentConnection.name;
    await loadHotspotCapabilities();
    element("hotspot-section").scrollIntoView({behavior:"smooth", block:"start"});
    const hotspotAlreadyConfigured = !element("hotspotStopButton").disabled;
    element("hotspotMessage").textContent = hotspotAlreadyConfigured
        ? tr(
            "ჯერ გამორთე მოქმედი Hotspot. შემდეგ Repeater რეჟიმში თავიდან ჩართე გაზიარება.",
            "Stop the currently active Hotspot first. Then start sharing again in Repeater mode."
        )
        : tr(
            "Repeater კავშირი არჩეულია. შეამოწმე WAN, მიუთითე Hotspot-ის პაროლი და ჩართე გაზიარება.",
            "Repeater connection selected. Confirm the WAN, enter a Hotspot password and start sharing."
        );
    if (!element("hotspotPassword").value) element("hotspotPassword").focus({preventScroll:true});
}

/* CONTROLS */

element("refreshButton").addEventListener("click", loadData);
element("scanButton").addEventListener("click", scanWifi);
element("repeaterDisconnectButton").addEventListener("click", disconnectRepeater);
element("repeaterShareButton").addEventListener("click", shareRepeaterConnection);


element("interfaceFilter").addEventListener(
    "change",
    loadInterfaces
);

/* Sidebar section tracking — keeps navigation context in sync while scrolling */
(() => {
    const links = Array.from(document.querySelectorAll(".primary-nav a[href^='#']"));
    const sections = links
        .map(link => document.querySelector(link.getAttribute("href")))
        .filter(Boolean);
    const activate = id => {
        links.forEach(link => {
            const active = link.getAttribute("href") === "#" + id;
            link.classList.toggle("active", active);
            if (active) link.setAttribute("aria-current", "location");
            else link.removeAttribute("aria-current");
        });
    };
    links.forEach(link => link.addEventListener("click", () => {
        const id = link.getAttribute("href").slice(1);
        activate(id);
    }));
    if ("IntersectionObserver" in window) {
        const observer = new IntersectionObserver(entries => {
            const visible = entries.filter(entry => entry.isIntersecting)
                .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
            if (visible) activate(visible.target.id);
        }, {rootMargin:"-18% 0px -68% 0px",threshold:[0,.15,.35,.6]});
        sections.forEach(section => observer.observe(section));
    } else {
        const initial = location.hash.slice(1) || "overview-section";
        activate(initial);
    }
})();

/* POLLING */

function startPolling() {
    stopPolling();

    if (!state.authenticated) return;

    loadData();
    refreshHotspotDnsActivity();
    loadDnsStatus();
    loadHotspotCapabilities();
    refreshSms(true);
    refreshZeroTier();

    state.zerotierPollingTimer = setInterval(() => {
        if (state.authenticated) refreshZeroTier();
    }, 30000);

    state.pollingTimer = setInterval(() => {
        if (state.authenticated) loadData();
    }, 15000);
    hotspotDnsPollingTimer = setInterval(refreshHotspotDnsActivity, 5000);
    state.smsPollingTimer = setInterval(() => {
        if (state.authenticated) refreshSms(true);
    }, 30000);
}

function stopPolling() {
    if (hotspotDnsPollingTimer !== null) {
        clearInterval(hotspotDnsPollingTimer);
        hotspotDnsPollingTimer = null;
    }
    if (state.pollingTimer !== null) {
        clearInterval(state.pollingTimer);
        state.pollingTimer = null;
    }
    if (state.smsPollingTimer !== null) {
        clearInterval(state.smsPollingTimer);
        state.smsPollingTimer = null;
    }
    if (state.zerotierPollingTimer !== null) {
        clearInterval(state.zerotierPollingTimer);
        state.zerotierPollingTimer = null;
    }
}

/* CHART RESIZE */

let resizeTimer = null;

window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);

    resizeTimer = setTimeout(() => {
        if (state.authenticated) {
            loadTrafficHistory().catch(console.error);
        }
    }, 250);
});


/* SIM SMS */
let smsMessagesCache = [];
let smsActiveFilter = "all";

function renderSmsMessages() {
    const list = element("smsList");
    list.replaceChildren();
    const visible = smsMessagesCache.filter(sms =>
        smsActiveFilter === "all" || sms.direction === smsActiveFilter
    );
    document.querySelectorAll("[data-sms-filter]").forEach(button => {
        const active = button.dataset.smsFilter === smsActiveFilter;
        button.classList.toggle("active", active);
        button.setAttribute("aria-pressed", String(active));
    });
    if (!visible.length) {
        list.textContent = smsMessagesCache.length
            ? "ამ ფილტრში შეტყობინებები არ არის."
            : "შეტყობინებები ვერ მოიძებნა.";
        return;
    }
    for (const sms of visible) {
        const card = document.createElement("article");
        card.className = "sms-item";
        const meta = document.createElement("div");
        meta.className = "sms-meta";
        const direction = document.createElement("strong");
        direction.textContent = sms.direction === "received" ? "შემოსული"
            : sms.direction === "sent" ? "გაგზავნილი" : "მიმართულება უცნობია";
        const number = document.createElement("span");
        number.textContent = sms.number || "უცნობი ნომერი";
        const time = document.createElement("span");
        time.textContent = sms.timestamp || sms.state || "";
        meta.append(direction, number, time);
        const body = document.createElement("div");
        body.className = "sms-body";
        body.textContent = sms.text || "(ტექსტი მიუწვდომელია)";
        const actions = document.createElement("div");
        actions.className = "sms-item-actions";
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "sms-delete-button";
        remove.textContent = "წაშლა";
        remove.addEventListener("click", () => deleteSmsMessage(sms));
        actions.append(remove);
        card.append(meta, body, actions);
        list.append(card);
    }
}

async function refreshSms(silent = false) {
    if (!state.authenticated || state.smsLoading) return;
    state.smsLoading = true;
    const status = element("smsStatus");
    if (!silent && status) status.textContent = "SMS-ები იტვირთება...";
    try {
        const data = await fetchJSON(API + "/cellular/sms/messages");
        smsMessagesCache = Array.isArray(data.messages) ? data.messages : [];
        trackSmsNotifications(smsMessagesCache);
        renderSmsMessages();
        if (!silent && status) status.textContent = "მოდემი #" + data.modem_id + " · " + data.count + " შეტყობინება";
    } catch (error) {
        if (!silent && status) status.textContent = "შეცდომა: " + error.message;
    } finally { state.smsLoading = false; }
}

async function deleteSmsMessage(sms) {
    if (!state.authenticated) return;
    const label = sms.number || "უცნობი ნომერი";
    if (!window.confirm("ნამდვილად წავშალო SMS ნომრიდან/ნომერზე " + label +
        "? ეს მოქმედება მოდემში შეტყობინებას სამუდამოდ წაშლის.")) return;
    const status = element("smsStatus");
    status.textContent = "SMS იშლება...";
    try {
        const response = await apiFetch(API + "/cellular/sms/messages/" +
            encodeURIComponent(sms.id), {method:"DELETE"});
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || ("HTTP " + response.status));
        status.textContent = "SMS წაიშალა · ID " + sms.id;
        addNotification("info", "SMS წაიშალა", "SMS deleted", "შეტყობინება " + (sms.number || sms.id) + " წაიშალა.", "Message " + (sms.number || sms.id) + " was deleted.");
        await refreshSms();
    } catch (error) {
        status.textContent = "წაშლის შეცდომა: " + error.message;
    }
}

element("smsRefreshButton")?.addEventListener("click", refreshSms);
document.querySelectorAll("[data-sms-filter]").forEach(button => {
    button.addEventListener("click", () => {
        smsActiveFilter = button.dataset.smsFilter;
        renderSmsMessages();
    });
});
element("smsMessage")?.addEventListener("input", event => {
    element("smsLength").textContent = String(event.target.value.length);
});
element("smsSendForm")?.addEventListener("submit", async event => {
    event.preventDefault();
    if (!state.authenticated) return;
    const number = element("smsNumber").value.trim();
    const message = element("smsMessage").value;
    if (!window.confirm("გავაგზავნო SMS ნომერზე " + number + "? ოპერატორმა შეიძლება ჩამოგაჭრას SMS-ის საფასური.")) return;
    const button = element("smsSendButton");
    button.disabled = true;
    element("smsStatus").textContent = "SMS იგზავნება...";
    try {
        const response = await apiFetch(API + "/cellular/sms/send", {
            method:"POST",
            headers:{"Content-Type":"application/json"},
            body:JSON.stringify({number, message})
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || ("HTTP " + response.status));
        element("smsMessage").value = "";
        element("smsLength").textContent = "0";
        element("smsStatus").textContent = "SMS მიღებულია გასაგზავნად · ID " + data.sms_id;
        addNotification("success", "SMS გაიგზავნა", "SMS sent", "ნომერზე " + number + " SMS გაიგზავნა.", "An SMS was sent to " + number + ".");
        refreshSms();
    } catch (error) {
        element("smsStatus").textContent = "შეცდომა: " + error.message;
    } finally {
        button.disabled = false;
    }
});

/* Authenticated PTY terminal */
let terminalSocket = null;
let terminalConnected = false;
function terminalWrite(value) {
    const output = element("terminalOutput");
    const readable = String(value)
        .replace(/\x1B\[[0-?]*[ -/]*[@-~]/g, "")
        .replace(/\x1B\][^\x07]*(?:\x07|\x1B\\)/g, "")
        .replace(/\r\n/g, "\n");
    output.textContent += readable;
    if (output.textContent.length > 150000) output.textContent = output.textContent.slice(-120000);
    const windowEl = element("terminalWindow");
    windowEl.scrollTop = windowEl.scrollHeight;
}
function terminalSetConnected(connected, label) {
    terminalConnected = connected;
    element("terminalStatus").textContent = label;
    element("terminalConnectButton").disabled = connected;
    element("terminalDisconnectButton").disabled = !connected;
    element("terminalInput").disabled = !connected;
    element("terminalSendButton").disabled = !connected;
    element("terminalCtrlC").disabled = !connected;
    element("terminalCtrlD").disabled = !connected;
}
function terminalSend(data) {
    if (terminalSocket && terminalSocket.readyState === WebSocket.OPEN) {
        terminalSocket.send(JSON.stringify({type:"input", data}));
    }
}
function connectTerminal() {
    if (!state.authenticated || terminalSocket) return;
    const scheme = location.protocol === "https:" ? "wss://" : "ws://";
    const socket = new WebSocket(scheme + location.host + "/api/v1/terminal/ws");
    terminalSocket = socket;
    terminalSetConnected(false, "უკავშირდება...");
    socket.onopen = () => terminalSetConnected(true, "დაკავშირებულია");
    socket.onmessage = event => {
        let packet;
        try { packet = JSON.parse(event.data); } catch (_) { return; }
        if (packet.type === "ready") {
            terminalWrite("\n[Keyland PTY] მომხმარებელი: " + packet.username +
                " · უფლებები: " + packet.privilege + " · სამუშაო დირექტორია: " + packet.cwd + "\n");
        } else if (packet.type === "output") {
            terminalWrite(packet.data);
        } else if (packet.type === "error") {
            terminalWrite("\n[შეცდომა] " + packet.message + "\n");
        }
    };
    socket.onerror = () => terminalSetConnected(false, "კავშირის შეცდომა");
    socket.onclose = event => {
        terminalSocket = null;
        terminalSetConnected(false, event.code === 1008 ? "ავტორიზაცია/Origin უარყოფილია" : "კავშირი დასრულდა");
    };
}
function disconnectTerminal() {
    if (terminalSocket) {
        try { terminalSocket.send(JSON.stringify({type:"close"})); } catch (_) {}
        terminalSocket.close();
        terminalSocket = null;
    }
    terminalSetConnected(false, "გამორთულია");
}
element("terminalConnectButton")?.addEventListener("click", connectTerminal);
element("terminalDisconnectButton")?.addEventListener("click", disconnectTerminal);
element("terminalSendButton")?.addEventListener("click", () => terminalSend("\r"));
element("terminalInput")?.addEventListener("keydown", event => {
    if (!terminalConnected || event.isComposing) return;
    let data = null;
    if (event.ctrlKey && !event.altKey && !event.metaKey) {
        const key = event.key.toLowerCase();
        const controls = {c:"\u0003", d:"\u0004", z:"\u001a", l:"\u000c", a:"\u0001", e:"\u0005", u:"\u0015", k:"\u000b", w:"\u0017"};
        data = controls[key] || null;
    } else if (event.key === "Enter") data = "\r";
    else if (event.key === "Backspace") data = "\u007f";
    else if (event.key === "Tab") data = "\t";
    else if (event.key === "Escape") data = "\u001b";
    else if (event.key === "ArrowUp") data = "\u001b[A";
    else if (event.key === "ArrowDown") data = "\u001b[B";
    else if (event.key === "ArrowRight") data = "\u001b[C";
    else if (event.key === "ArrowLeft") data = "\u001b[D";
    else if (event.key === "Home") data = "\u001b[H";
    else if (event.key === "End") data = "\u001b[F";
    else if (event.key === "Delete") data = "\u001b[3~";
    else if (event.key.length === 1 && !event.altKey && !event.metaKey) data = event.key;
    if (data !== null) {
        event.preventDefault();
        terminalSend(data);
        event.target.value = "";
    }
});
element("terminalInput")?.addEventListener("paste", event => {
    if (!terminalConnected) return;
    event.preventDefault();
    terminalSend(event.clipboardData.getData("text"));
});
element("terminalInput")?.addEventListener("compositionend", event => {
    if (terminalConnected && event.data) terminalSend(event.data);
    event.target.value = "";
});
element("terminalCtrlC")?.addEventListener("click", () => terminalSend("\u0003"));
element("terminalCtrlD")?.addEventListener("click", () => terminalSend("\u0004"));
element("terminalClearButton")?.addEventListener("click", () => {
    element("terminalOutput").textContent = "";
});




/* Move long explanatory copy behind a compact help affordance. */
(function(){
  document.querySelectorAll("#dashboard .card > p:not([id]):not([role])").forEach(paragraph=>{
    if(paragraph.textContent.trim().length<110 || paragraph.querySelector("button,input,select,textarea,a")) return;
    const details=document.createElement("details");
    details.className="inline-help";
    const summary=document.createElement("summary");
    summary.textContent="?";
    summary.setAttribute("aria-label","დამატებითი განმარტება");
    details.appendChild(summary);
    details.appendChild(paragraph);
    paragraph.parentNode.insertBefore(details,paragraph);
  });
})();

/* Page views: keep the overview concise and move technical controls to focused pages. */
(function(){
  const titles={"overview":"მიმოხილვა","mobile":"მობილური ქსელი","hotspot":"Hotspot","portal":"Captive Portal","activity":"მოწყობილობების აქტივობა","repeater":"Wi-Fi გამეორება","zerotier":"ZeroTier","network":"ქსელის მართვა","system":"სისტემა"};
  function setView(view){
    if(!titles[view]) view="overview";
    document.body.dataset.activeView=view;
    const title=document.getElementById("pageTitle");
    if(title) title.textContent=titles[view];
    document.querySelectorAll(".primary-nav [data-view-target]").forEach(link=>{
      if(link.dataset.viewTarget===view) link.setAttribute("aria-current","page");
      else link.removeAttribute("aria-current");
    });
    const active=document.querySelector('.primary-nav [data-view-target="'+view+'"]');
    if(active) active.scrollIntoView({block:"nearest",inline:"nearest"});
    try{history.replaceState(null,"","#"+view);}catch(_){}
  }
  document.querySelectorAll(".primary-nav [data-view-target]").forEach(link=>{
    link.addEventListener("click",event=>{event.preventDefault();setView(link.dataset.viewTarget);});
  });
  function read(id){return document.getElementById(id)?.textContent?.trim()||"";}
  function syncSummary(){
    const internet=read("internetStatus");
    const clients=read("hotspotClientCount");
    const battery=read("batteryLevel");
    const set=(id,value)=>{
      const node=document.getElementById(id);
      if(!node)return;
      node.textContent=(!value||/^(--|მონაცემი ჯერ არ არის|მოწმდება|მოლოდინი)/i.test(value))?"მონაცემი ჯერ არ არის":value;
    };
    set("summaryInternet",internet);
    set("summaryClients",clients);
    set("summaryBattery",battery);
  }
  document.body.dataset.activeView="overview";
  syncSummary();
  const observer=new MutationObserver(syncSummary);
  ["internetStatus","hotspotClientCount","batteryLevel"].forEach(id=>{
    const node=document.getElementById(id);if(node)observer.observe(node,{childList:true,subtree:true,characterData:true});
  });
  document.getElementById("pageTitle")?.addEventListener("dblclick",()=>setView("overview"));
})();

/* START */

initializeApp();

