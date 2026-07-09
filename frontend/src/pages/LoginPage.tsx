import { useEffect, useState } from "react";
import { useLocation } from "wouter";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Eye, EyeOff, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { loginClientCode, loginAdmin } from "@/lib/api";
import { setSession } from "@/lib/storage";

export default function LoginPage() {
  const [, setLocation] = useLocation();
  const [activeTab, setActiveTab] = useState("client");

  const [clientJobCode, setClientJobCode] = useState("");
  const [clientPhone, setClientPhone] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);

  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    document.title = "RMA | Login";
  }, []);

  const handleClientLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    
    const normalizedCode = clientJobCode.trim().toUpperCase();
    const normalizedPhone = clientPhone.trim();
    if (!normalizedPhone) {
      setError("Please enter your registered phone number.");
      return;
    }
    if (normalizedPhone.replace(/\D/g, "").length < 10) {
      setError("Please enter a valid phone number.");
      return;
    }
    if (!normalizedCode) {
      setError("Please enter your Client Job Code.");
      return;
    }
    if (normalizedCode.length > 80) {
      setError("Client Job Code is too long.");
      return;
    }

    setIsLoading(true);
    try {
      const data = await loginClientCode(normalizedCode, normalizedPhone);
      setSession({
        token: data.access_token,
        user: {
          name: data.employee_name || "Client",
          role: "client",
          clientJobCode: data.client_job_code || normalizedCode,
        },
      });

      toast.success("Login successful");
      setLocation("/chat");
    } catch (err: any) {
      setError(err.message || "Failed to login. Please try again.");
    } finally {
      setIsLoading(false);
    }
  };

  const handleAdminLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    if (!username || !password) {
      setError("Please enter username and password.");
      return;
    }

    setIsLoading(true);
    try {
      const data = await loginAdmin(username, password);
      setSession({
        token: data.access_token,
        user: {
          name: data.employee_name || "Admin",
          role: "admin",
        },
      });

      toast.success("Admin login successful");
      setLocation("/chat");
    } catch (err: any) {
      setError(err.message || "Failed to login. Please try again.");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-[100dvh] flex items-center justify-center bg-background p-4">
      <div className="w-full max-w-md bg-card border border-border rounded-xl shadow-lg overflow-hidden">
        <div className="p-8 text-center border-b border-border">
          <img
            src="/RMA.png"
            alt="RMA — Rahul Mishra & Associates"
            className="mx-auto h-20 w-auto object-contain"
          />
          <p className="text-sm font-medium text-primary mt-4">
            Rahul Mishra &amp; Associates | Chartered Accountants
          </p>
          <div className="w-12 h-[2px] bg-accent mx-auto mt-4 mb-3"></div>
          <p className="text-xs font-semibold text-accent uppercase tracking-wider">
            Finance FMS Client Portal
          </p>
        </div>

        <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
          <TabsList className="w-full flex border-b border-border rounded-none p-0 h-auto bg-transparent">
            <TabsTrigger
              value="client"
              className="flex-1 py-4 rounded-none data-[state=active]:bg-background data-[state=active]:border-b-2 data-[state=active]:border-primary data-[state=active]:shadow-none font-semibold"
            >
              Client Login
            </TabsTrigger>
            <TabsTrigger
              value="admin"
              className="flex-1 py-4 rounded-none data-[state=active]:bg-background data-[state=active]:border-b-2 data-[state=active]:border-primary data-[state=active]:shadow-none font-semibold"
            >
              Admin Login
            </TabsTrigger>
          </TabsList>

          <div className="p-8">
            <TabsContent value="client" className="m-0 focus-visible:outline-none">
              <form onSubmit={handleClientLogin} className="space-y-6">
                <div className="space-y-2">
                  <Label htmlFor="client-phone" className="text-sm font-medium">
                    Registered Phone Number
                  </Label>
                  <Input
                    id="client-phone"
                    type="tel"
                    inputMode="tel"
                    autoComplete="tel"
                    maxLength={20}
                    value={clientPhone}
                    onChange={(e) => setClientPhone(e.target.value)}
                    placeholder="Enter your registered mobile number"
                    className="h-12"
                    disabled={isLoading}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="client-job-code" className="text-sm font-medium">
                    Client Job Code
                  </Label>
                  <Input
                    id="client-job-code"
                    type="text"
                    autoCapitalize="characters"
                    maxLength={80}
                    value={clientJobCode}
                    onChange={(e) => setClientJobCode(e.target.value.toUpperCase())}
                    placeholder="Enter Client Job Code"
                    className="h-12"
                    disabled={isLoading}
                  />
                  {error && activeTab === "client" && (
                    <p className="text-sm text-destructive font-medium">{error}</p>
                  )}
                </div>
                <Button
                  type="submit"
                  className="w-full h-12 bg-primary hover:bg-primary/90 text-accent font-semibold transition-colors"
                  disabled={isLoading}
                >
                  {isLoading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                  Login with Client Job Code
                </Button>
              </form>
            </TabsContent>

            <TabsContent value="admin" className="m-0 focus-visible:outline-none">
              <form onSubmit={handleAdminLogin} className="space-y-6">
                <div className="space-y-4">
                  <div className="space-y-2">
                    <Label htmlFor="username" className="text-sm font-medium">
                      Username
                    </Label>
                    <Input
                      id="username"
                      type="text"
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                      placeholder="Admin username"
                      className="h-12"
                      disabled={isLoading}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="password" className="text-sm font-medium">
                      Password
                    </Label>
                    <div className="relative">
                      <Input
                        id="password"
                        type={showPassword ? "text" : "password"}
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        placeholder="••••••••"
                        className="h-12 pr-10"
                        disabled={isLoading}
                      />
                      <button
                        type="button"
                        onClick={() => setShowPassword(!showPassword)}
                        className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                        disabled={isLoading}
                      >
                        {showPassword ? <EyeOff className="h-5 w-5" /> : <Eye className="h-5 w-5" />}
                      </button>
                    </div>
                  </div>
                  {error && activeTab === "admin" && (
                    <p className="text-sm text-destructive font-medium">{error}</p>
                  )}
                </div>
                <Button
                  type="submit"
                  className="w-full h-12 bg-primary hover:bg-primary/90 text-accent font-semibold transition-colors"
                  disabled={isLoading}
                >
                  {isLoading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                  Admin Login
                </Button>
              </form>
            </TabsContent>
          </div>
        </Tabs>
      </div>
    </div>
  );
}
