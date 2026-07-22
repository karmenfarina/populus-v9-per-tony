// Firebase client SDK bootstrap for the Populus Expo app.
// Only Email/Password Auth is used here — Google Sign-In continues
// to go through Emergent's OAuth flow, and anonymous accounts stay
// on our custom backend endpoint. Firebase gives us email verification
// + password reset with zero email infrastructure.
import { initializeApp, getApps, getApp } from "firebase/app";
import {
  getAuth,
  initializeAuth,
  createUserWithEmailAndPassword,
  signInWithEmailAndPassword,
  sendEmailVerification,
  sendPasswordResetEmail,
  signOut as fbSignOut,
  reload as fbReload,
} from "firebase/auth";
import { Platform } from "react-native";

const firebaseConfig = {
  apiKey: "AIzaSyASZpoqG4_RdwGVQuvjO9YYZ5E0fpdShKo",
  authDomain: "populus-1f567.firebaseapp.com",
  projectId: "populus-1f567",
  storageBucket: "populus-1f567.firebasestorage.app",
  messagingSenderId: "931872462409",
  appId: "1:931872462409:web:8b91baafdfc2f4c6b11198",
};

const app = getApps().length ? getApp() : initializeApp(firebaseConfig);

// On native (iOS/Android) we ideally would use AsyncStorage-backed
// persistence via `getReactNativePersistence`, but the modular v10+
// SDK doesn't ship it publicly anymore. `getAuth()` falls back to
// in-memory persistence on native and localStorage on web — good
// enough for MVP: our backend session_token (returned by
// /auth/firebase-session) is what survives across launches.
let auth: any;
try {
  auth = Platform.OS === "web" ? getAuth(app) : initializeAuth(app);
} catch {
  auth = getAuth(app);
}

export {
  auth,
  createUserWithEmailAndPassword,
  signInWithEmailAndPassword,
  sendEmailVerification,
  sendPasswordResetEmail,
  fbSignOut,
  fbReload,
};
