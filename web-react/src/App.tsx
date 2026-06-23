import { Routes, Route } from "react-router-dom";
import { AuthProvider, RequireAuth } from "@/lib/auth";
import { AppShell } from "@/components/AppShell";
import LoginPage from "@/features/auth/LoginPage";
import LiveWall from "@/features/live/LiveWall";
import NvrManagement from "@/features/nvrs/NvrManagement";
import CameraChannels from "@/features/nvrs/CameraChannels";
import SettingsPage from "@/features/settings/SettingsPage";

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          element={
            <RequireAuth>
              <AppShell />
            </RequireAuth>
          }
        >
          <Route index element={<LiveWall />} />
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
          <Route path="settings" element={<SettingsPage />} />
        </Route>
      </Routes>
    </AuthProvider>
  );
}
