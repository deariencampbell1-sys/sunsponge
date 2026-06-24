const Icon = ({ children, size = 16, strokeWidth = 1.6, ...rest }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth={strokeWidth}
    strokeLinecap="round"
    strokeLinejoin="round"
    {...rest}
  >
    {children}
  </svg>
);

const IconCamera = (p) => (
  <Icon {...p}>
    <path d="M14.5 4 13 2H8L6.5 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2Z" />
    <circle cx="12" cy="12" r="3.5" />
  </Icon>
);
const IconUpload = (p) => (
  <Icon {...p}>
    <path d="M12 16V4" />
    <path d="m5 11 7-7 7 7" />
    <path d="M4 20h16" />
  </Icon>
);
const IconDownload = (p) => (
  <Icon {...p}>
    <path d="M12 4v12" />
    <path d="m5 9 7 7 7-7" />
    <path d="M4 20h16" />
  </Icon>
);
const IconPlay = (p) => (
  <Icon {...p}>
    <path d="M8 5v14l11-7Z" />
  </Icon>
);
const IconCheckCircle = (p) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="m8 12 2.5 2.5L16 9" />
  </Icon>
);
const IconAlertCircle = (p) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7v6" />
    <path d="M12 17h.01" />
  </Icon>
);
const IconFolder = (p) => (
  <Icon {...p}>
    <path d="M20 20H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2Z" />
  </Icon>
);
const IconFile = (p) => (
  <Icon {...p}>
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" />
    <path d="M14 2v6h6" />
  </Icon>
);

Object.assign(window, {
  IconCamera, IconUpload, IconDownload, IconPlay, IconCheckCircle, IconAlertCircle, IconFolder, IconFile,
});