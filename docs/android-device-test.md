# Blood Connect — Test on an Android phone

This repository contains both the FastAPI backend (repository root) and the
original Expo/React Native frontend (`frontend/`). Android native source lives
in `frontend/android/`.

## 1. Start the backend on your laptop

Install `uv` and Python 3.12, then from the **repository root**:

```bash
uv sync
cp .env.example .env
# Set SECRET_KEY to a long random secret in .env
uv run alembic upgrade head
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

In another terminal (same directory, same `.env`):

```bash
uv run python -m app.workers.notification_worker
```

On the laptop, open `http://localhost:8000/health`. On your **phone browser**
on the same Wi-Fi, open `http://YOUR_LAPTOP_LAN_IP:8000/health`. Both should
respond with JSON status `healthy` **before** testing the app. Find the IP
with `hostname -I` on Linux. Firewall should allow port 8000 only from your
trusted local subnet.

## 2. Install an APK without Android Studio

Open the latest successful **Blood Connect Android** GitHub Actions run on the
integration branch, download the `blood-connect-android-test-apk` artifact,
unzip it, and install its `app-release.apk` on an Android phone. The archive is
an artifact, not a GitHub Release. You must be signed into GitHub to download
it. This is a **TEST BUILD ONLY**, signed with a disposable debug signing key.
Do not upload it to Play Store and do not store personal medical data in this
test environment.

The APK includes the React Native JS bundle, so Metro is not required.
It searches your Wi-Fi network for a backend at port 8000 when no
`EXPO_PUBLIC_API_URL` was supplied during build. If this fails, rebuild locally
with `frontend/.env` containing `EXPO_PUBLIC_API_URL=http://YOUR_LAN_IP:8000`.
It will not reach a backend on another network automatically.

## 3. Accounts and permissions

Email/password signup works without Google/Firebase credentials. In development
mode, the backend writes the email verification code to its **local**
`dev_outbox/` directory unless SMTP is configured. Read the local message,
enter the six-digit code on your phone, and complete the donor profile.

Google sign-in requires matching Google OAuth client IDs and Android signing
SHA-1 registration. Firebase push requires a valid `google-services.json`
and Firebase Admin credentials. Without those credentials, **in-app
notifications** work; external push does not. The app must still be usable.

## 4. Build locally with the existing native project (optional)

Prerequisites: Node 22, JDK 17, Android SDK/NDK configured, a phone with USB
debugging and `adb` installed.

```bash
cd frontend
npm ci
# Create frontend/.env from .env.example if you need a fixed API URL.
# Native build requires a local debug keystore if one is not already present:
keytool -genkeypair -keystore android/app/debug.keystore -storepass android \
  -alias androiddebugkey -keypass android -keyalg RSA -keysize 2048 \
  -validity 3650 -dname 'CN=Android Debug,O=Android,C=US'
npm run android
```

The local **debug** build needs Metro while the application is running.
Use the standalone test APK from GitHub Actions if you want no Metro.
Native Firebase and Google modules mean the original app is **not** an Expo
Go-compatible project without further adaptation.

## 5. Verify with two test accounts

Using separate donor and recipient accounts on two devices (or changing
accounts sequentially), test: signup + verification, complete profile,
create request with coordinates, compatible nearby search, accept a unit,
see it in Donations, confirm a real commitment, release an uncompleted
donor, withdraw, and view notifications/history. Use synthetic patient
details only during testing. Donor compatibility and eligibility still require
professional clinical screening before an actual transfusion.

Review known limitations before deploying to public users: no independent
device-test evidence yet, no SMS verification or abuse monitoring, LAN-only
backend setup, and no production signing/Google/Firebase setup.
