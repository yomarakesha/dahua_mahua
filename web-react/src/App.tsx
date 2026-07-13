import { Routes, Route } from "react-router-dom";
import { AuthProvider, RequireAuth } from "@/lib/auth";
import { AppShell } from "@/components/AppShell";
import LoginPage from "@/features/auth/LoginPage";
import LiveWall from "@/features/live/LiveWall";
import NvrManagement from "@/features/nvrs/NvrManagement";
import CameraChannels from "@/features/nvrs/CameraChannels";
import UsersPage from "@/features/users/UsersPage";
import SettingsPage from "@/features/settings/SettingsPage";
import PlaybackPage from "@/features/playback/PlaybackPage";
import LicensePage from "@/features/license/LicensePage";
import { LicenseGate } from "@/features/license/LicenseGate";
import SetupWizard from "@/features/setup/SetupWizard";
import { SetupGate } from "@/features/setup/SetupGate";

export default function App() {
  return (
    <AuthProvider>
      <LicenseGate>
        <Routes>
        <Route path="/login" element={<LoginPage />} />
        {/* First-run setup — full-screen (outside AppShell), admin-only. */}
        <Route
          path="/setup"
          element={
            <RequireAuth adminOnly>
              <SetupWizard />
            </RequireAuth>
          }
        />
        <Route
          element={
            <RequireAuth>
              <AppShell />
            </RequireAuth>
          }
        >
          <Route
            index
            element={
              <SetupGate>
                <LiveWall />
              </SetupGate>
            }
          />
          <Route
            path="nvrs"
            element={
              <RequireAuth adminOnly>
                <NvrManagement />
              </RequireAuth>
            }
          />
          <Route
            path="nvrs/:nvrId/channels"
            element={
              <RequireAuth adminOnly>
                <CameraChannels />
              </RequireAuth>
            }
          />
          <Route
            path="users"
            element={
              <RequireAuth adminOnly>
                <UsersPage />
              </RequireAuth>
            }
          />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="playback" element={<PlaybackPage />} />
          <Route path="license" element={<LicensePage />} />
        </Route>
        </Routes>
      </LicenseGate>
    </AuthProvider>
  );
}
