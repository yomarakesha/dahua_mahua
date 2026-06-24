import type { SVGProps } from "react";

/** Shared inline icons (stroke-based, currentColor) — no icon dependency. */
function Ic({ children, size = 16, ...p }: SVGProps<SVGSVGElement> & { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      {...p}
    >
      {children}
    </svg>
  );
}

export const GridIcon = (p: SVGProps<SVGSVGElement> & { size?: number }) => (
  <Ic {...p}>
    <rect x="3" y="3" width="7" height="7" rx="1" />
    <rect x="14" y="3" width="7" height="7" rx="1" />
    <rect x="3" y="14" width="7" height="7" rx="1" />
    <rect x="14" y="14" width="7" height="7" rx="1" />
  </Ic>
);
export const ServerIcon = (p: SVGProps<SVGSVGElement> & { size?: number }) => (
  <Ic {...p}>
    <rect x="3" y="4" width="18" height="7" rx="2" />
    <rect x="3" y="13" width="18" height="7" rx="2" />
    <path d="M7 7.5h.01M7 16.5h.01" />
  </Ic>
);
export const GearIcon = (p: SVGProps<SVGSVGElement> & { size?: number }) => (
  <Ic {...p}>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6h.09A1.65 1.65 0 0 0 11 3.09V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
  </Ic>
);
export const PowerIcon = (p: SVGProps<SVGSVGElement> & { size?: number }) => (
  <Ic {...p}>
    <path d="M18.36 6.64a9 9 0 1 1-12.73 0M12 2v10" />
  </Ic>
);
export const SearchIcon = (p: SVGProps<SVGSVGElement> & { size?: number }) => (
  <Ic {...p}>
    <circle cx="11" cy="11" r="7" />
    <path d="M21 21l-4-4" />
  </Ic>
);
export const ChevronDown = (p: SVGProps<SVGSVGElement> & { size?: number }) => (
  <Ic {...p}>
    <path d="M6 9l6 6 6-6" />
  </Ic>
);
export const ChevronRight = (p: SVGProps<SVGSVGElement> & { size?: number }) => (
  <Ic {...p}>
    <path d="M9 6l6 6-6 6" />
  </Ic>
);
export const PlusIcon = (p: SVGProps<SVGSVGElement> & { size?: number }) => (
  <Ic {...p}>
    <path d="M12 5v14M5 12h14" />
  </Ic>
);
export const PlayIcon = (p: SVGProps<SVGSVGElement> & { size?: number }) => (
  <Ic {...p} fill="currentColor" stroke="none">
    <path d="M6 4l14 8-14 8z" />
  </Ic>
);
export const SparkleIcon = (p: SVGProps<SVGSVGElement> & { size?: number }) => (
  <Ic {...p}>
    <path d="M5 3v4M3 5h4M19 17v4M17 19h4" />
    <path d="M12 3l2.5 5.5L20 11l-5.5 2.5L12 19l-2.5-5.5L4 11l5.5-2.5z" />
  </Ic>
);
export const RefreshIcon = (p: SVGProps<SVGSVGElement> & { size?: number }) => (
  <Ic {...p}>
    <path d="M21 12a9 9 0 1 1-3-6.7L21 8" />
    <path d="M21 3v5h-5" />
  </Ic>
);
export const TrashIcon = (p: SVGProps<SVGSVGElement> & { size?: number }) => (
  <Ic {...p}>
    <path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" />
  </Ic>
);
export const CheckIcon = (p: SVGProps<SVGSVGElement> & { size?: number }) => (
  <Ic {...p}>
    <path d="M20 6L9 17l-5-5" />
  </Ic>
);
export const XIcon = (p: SVGProps<SVGSVGElement> & { size?: number }) => (
  <Ic {...p}>
    <path d="M18 6L6 18M6 6l12 12" />
  </Ic>
);
export const PencilIcon = (p: SVGProps<SVGSVGElement> & { size?: number }) => (
  <Ic {...p}>
    <path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" />
  </Ic>
);
export const CameraIcon = (p: SVGProps<SVGSVGElement> & { size?: number }) => (
  <Ic {...p}>
    <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z" />
    <circle cx="12" cy="13" r="4" />
  </Ic>
);
export const ActivityIcon = (p: SVGProps<SVGSVGElement> & { size?: number }) => (
  <Ic {...p}>
    <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
  </Ic>
);
export const ExpandIcon = (p: SVGProps<SVGSVGElement> & { size?: number }) => (
  <Ic {...p}>
    <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
  </Ic>
);
export const KeyIcon = (p: SVGProps<SVGSVGElement> & { size?: number }) => (
  <Ic {...p}>
    <circle cx="7.5" cy="15.5" r="4.5" />
    <path d="M10.5 12.5L21 2M16 7l3 3M14 9l2 2" />
  </Ic>
);
export const PauseIcon = (p: SVGProps<SVGSVGElement> & { size?: number }) => (
  <Ic {...p} fill="currentColor" stroke="none">
    <rect x="6" y="4" width="4" height="16" rx="1" />
    <rect x="14" y="4" width="4" height="16" rx="1" />
  </Ic>
);
export const VolumeOn = (p: SVGProps<SVGSVGElement> & { size?: number }) => (
  <Ic {...p}>
    <path d="M11 5L6 9H2v6h4l5 4z" />
    <path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13" />
  </Ic>
);
export const VolumeOff = (p: SVGProps<SVGSVGElement> & { size?: number }) => (
  <Ic {...p}>
    <path d="M11 5L6 9H2v6h4l5 4z" />
    <path d="M22 9l-6 6M16 9l6 6" />
  </Ic>
);
export const UsersIcon = (p: SVGProps<SVGSVGElement> & { size?: number }) => (
  <Ic {...p}>
    <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
    <circle cx="9" cy="7" r="4" />
    <path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" />
  </Ic>
);
export const Eye = (p: SVGProps<SVGSVGElement> & { size?: number }) => (
  <Ic {...p}>
    <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z" />
    <circle cx="12" cy="12" r="3" />
  </Ic>
);
export const EyeOff = (p: SVGProps<SVGSVGElement> & { size?: number }) => (
  <Ic {...p}>
    <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
    <path d="M1 1l22 22" />
  </Ic>
);
