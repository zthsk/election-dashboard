import "./globals.css";

export const metadata = {
  title: "Nepal Election Live",
  description: "Nepal Election 2079/2082 live result dashboard",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
