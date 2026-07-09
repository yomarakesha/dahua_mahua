/**
 * App-wide React error boundary. On a render/lifecycle crash it records the
 * component stack, ships the diagnostics ring to the backend, and renders a
 * dark-theme fallback with a Reload button. Dependency-free (a plain class
 * component) so it can catch errors even if other providers are broken.
 */
import { Component, type ErrorInfo, type ReactNode } from "react";
import { recordEvent, shipLogs } from "@/lib/diagnostics";
import i18n from "@/i18n";

interface Props {
  children: ReactNode;
}
interface State {
  hasError: boolean;
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    const stack = (info.componentStack ?? "").slice(0, 1500);
    recordEvent("react-crash", `${error.name}: ${error.message} | ${stack}`);
    shipLogs("react-crash");
  }

  render(): ReactNode {
    if (!this.state.hasError) return this.props.children;
    // Class component (may render even if React context is broken), so read the
    // i18n singleton directly rather than the useTranslation() hook.
    const t = i18n.t.bind(i18n);
    return (
      <div className="flex h-full min-h-screen w-full flex-col items-center justify-center gap-4 bg-deep text-center">
        <div className="flex items-center gap-1.5 rounded border border-danger/40 bg-danger/[.14] px-2 py-1 font-mono text-3xs font-bold uppercase tracking-wider text-danger">
          <span className="h-1.5 w-1.5 rounded-full bg-danger" />
          {t("errorBoundary.badge")}
        </div>
        <div className="text-base font-semibold text-ink-mute">{t("errorBoundary.title")}</div>
        <div className="max-w-sm text-2xs text-ink-faint">{t("errorBoundary.detail")}</div>
        <button
          type="button"
          onClick={() => location.reload()}
          className="rounded-md border border-white/10 bg-white/[.05] px-3 py-1.5 text-xs font-semibold text-ink-soft transition hover:bg-white/[.1]"
        >
          {t("errorBoundary.reload")}
        </button>
      </div>
    );
  }
}
