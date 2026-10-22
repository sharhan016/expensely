import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "./globals.css";
import "./workspace.css";
import "./finance.css";
import "./card-semantics.css";
import "./password.css";
import "./account-binding.css";
import "./management.css";

export const metadata = {
  title: "Expense Analyzer",
  description: "A private, statement-first financial workspace",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>{children}</body>
    </html>
  );
}
