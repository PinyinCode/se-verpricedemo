(function(){
'use strict';
const overlay=document.getElementById('accountAuthOverlay');
const form=document.getElementById('accountEmailForm');
const emailEl=document.getElementById('accountEmail');
const passEl=document.getElementById('accountPassword');
const msg=document.getElementById('accountAuthMsg');
const googleBtn=document.getElementById('accountGoogleBtn');
const loginBtn=document.getElementById('accountLoginBtn');
const dock=document.getElementById('accountAuthDock');
const emailLabel=document.getElementById('accountUserEmail');
let auth=null,db=null,checking=false;
const RECHECK_MS=15*60*1000;
const AUTH_CACHE_KEY='forex_account_auth_cache_v1';
window.accountAuthGranted=false;
function readAuthCache(user){
 try{
  const cached=JSON.parse(localStorage.getItem(AUTH_CACHE_KEY)||'null');
  return !!(cached&&user&&cached.uid===user.uid&&cached.granted===true);
 }catch(_){return false;}
}
function writeAuthCache(user){
 try{localStorage.setItem(AUTH_CACHE_KEY,JSON.stringify({uid:user.uid,email:user.email||'',granted:true,savedAt:Date.now()}));}catch(_){}
}
function clearAuthCache(){
 try{localStorage.removeItem(AUTH_CACHE_KEY);}catch(_){}
}
function show(message){msg.textContent=message||'';}
function lock(message){
 window.accountAuthGranted=false;
 overlay.hidden=false;
 dock.style.display='none';
 document.body.classList.add('account-auth-locked');
 window.dispatchEvent(new Event('forex-auth-locked'));
 if(message)show(message);
}
function unlock(user){
 window.accountAuthGranted=true;
 overlay.hidden=true;
 dock.style.display='flex';
 emailLabel.textContent=user.email||'Tài khoản';
 document.body.classList.remove('account-auth-locked');
 show('');
 window.dispatchEvent(new Event('forex-auth-ready'));
}
function firebaseError(e){
 const map={
 'auth/invalid-credential':'Email hoặc mật khẩu không đúng.',
 'auth/user-not-found':'Không tìm thấy tài khoản.',
 'auth/wrong-password':'Mật khẩu không đúng.',
 'auth/too-many-requests':'Đăng nhập quá nhiều lần. Thử lại sau.',
 'auth/popup-closed-by-user':'Bạn đã đóng cửa sổ đăng nhập Google.',
 'auth/unauthorized-domain':'Tên miền hiện tại chưa được thêm vào Firebase Authentication.'
 };
 return map[e.code]||e.message||'Không thể đăng nhập.';
}
async function checkPermission(user){
 if(!user||!user.email)throw new Error('Tài khoản không có email.');
 const snap=await db.collection('allowed_users').doc(user.email.toLowerCase()).get();
 if(!snap.exists)throw new Error('Tài khoản chưa được cấp quyền truy cập. Liên hệ quản trị viên.');
 const data=snap.data()||{};
 const role=String(data.role||'user').toLowerCase();
 const expiry=data.expiresAt&&typeof data.expiresAt.toDate==='function'?data.expiresAt.toDate():new Date(data.expiresAt);
 if(role!=='admin'&&role!=='super_admin'&&(!data.expiresAt||Number.isNaN(expiry.getTime())||Date.now()>expiry.getTime())){
  throw new Error('Tài khoản đã hết hạn hoặc chưa có hạn sử dụng.');
 }
 return true;
}
// Kiểm tra lại quyền khi đã mở từ cache: hết hạn / bị thu hồi thì khóa, lỗi mạng thì giữ phiên.
async function recheck(user){
 try{
  await checkPermission(user);
 }catch(e){
  if(e.code&&e.code!=='permission-denied')return;
  if(!auth.currentUser||auth.currentUser.uid!==user.uid)return;
  clearAuthCache();
  lock(firebaseError(e));
 }
}
async function onUser(user){
 if(checking)return;
 if(!user){clearAuthCache();lock('Đăng nhập để tiếp tục.');return;}
 // Mở ngay bằng phiên đã xác thực trước đó, rồi kiểm tra lại ở nền.
 if(readAuthCache(user)){unlock(user);recheck(user);return;}
 checking=true;loginBtn.disabled=true;googleBtn.disabled=true;show('Đang kiểm tra quyền truy cập...');
 try{
  await checkPermission(user);
  writeAuthCache(user);
  unlock(user);
 }catch(e){
  clearAuthCache();
  lock(firebaseError(e));
 }finally{
  checking=false;loginBtn.disabled=false;googleBtn.disabled=false;
 }
}
function init(){
 try{
  const cfg=window.FOREX_FIREBASE_CONFIG;
  if(!cfg||!cfg.apiKey||!cfg.projectId)throw new Error('Thiếu cấu hình Firebase trong config.js.');
  if(!firebase.apps.length)firebase.initializeApp(cfg);
  auth=firebase.auth();db=firebase.firestore();
  auth.onAuthStateChanged(onUser,e=>lock(firebaseError(e)));
  setInterval(()=>{if(window.accountAuthGranted&&auth.currentUser)recheck(auth.currentUser);},RECHECK_MS);
 }catch(e){lock(e.message||String(e));}
}
googleBtn.addEventListener('click',async()=>{
 try{show('Đang mở Google...');const provider=new firebase.auth.GoogleAuthProvider();await auth.signInWithPopup(provider);}
 catch(e){show(firebaseError(e));}
});
form.addEventListener('submit',async e=>{
 e.preventDefault();
 const email=emailEl.value.trim().toLowerCase(),password=passEl.value;
 if(!email||!password){show('Nhập email và mật khẩu.');return;}
 loginBtn.disabled=true;show('Đang đăng nhập...');
 try{await auth.signInWithEmailAndPassword(email,password);}
 catch(err){show(firebaseError(err));loginBtn.disabled=false;}
});
document.getElementById('accountForgotBtn').addEventListener('click',async()=>{
 const email=emailEl.value.trim();
 if(!email){show('Nhập email trước khi yêu cầu đặt lại mật khẩu.');return;}
 try{await auth.sendPasswordResetEmail(email);show('Đã gửi email đặt lại mật khẩu.');}
 catch(e){show(firebaseError(e));}
});
document.getElementById('accountSignOutBtn').addEventListener('click',async()=>{
 clearAuthCache();
 lock('Đang đăng xuất...');
 try{await auth.signOut();}catch(e){show(firebaseError(e));}
});
lock('Đang khởi tạo đăng nhập...');
init();
})();