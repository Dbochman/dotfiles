import javax.net.ssl.*;
import javax.naming.ldap.LdapName;
import java.net.*;
import java.security.cert.*;
import java.util.*;
import java.util.concurrent.*;

// Credential-free TLS handshakes; transport errors are never a passing test.
class TlsProbe {
  record Target(String host, String issuer) {}
  static String probe(Target target) {
    try (SSLSocket socket = (SSLSocket) SSLSocketFactory.getDefault().createSocket()) {
      socket.connect(new InetSocketAddress(target.host(), 443), 8000);
      socket.setSoTimeout(8000);
      SSLParameters parameters = socket.getSSLParameters();
      parameters.setEndpointIdentificationAlgorithm("HTTPS");
      parameters.setServerNames(List.of(new SNIHostName(target.host())));
      socket.setSSLParameters(parameters);
      socket.startHandshake();
      X509Certificate cert = (X509Certificate) socket.getSession().getPeerCertificates()[0];
      String cn = "";
      for (var rdn : new LdapName(cert.getIssuerX500Principal().getName()).getRdns()) {
        if (rdn.getType().equalsIgnoreCase("CN")) cn = rdn.getValue().toString();
      }
      return target.host() + " " + (target.issuer() == null ? "control_passed" : target.issuer().equals(cn) ? "exposed" : "canary_changed");
    } catch (Exception error) {
      boolean trustFailure = false;
      for (Throwable cause = error; cause != null; cause = cause.getCause()) {
        if (cause instanceof CertPathBuilderException) trustFailure = true;
        if (cause instanceof CertPathValidatorException validator) {
          if (validator.getReason() == CertPathValidatorException.BasicReason.REVOKED || validator.getReason() == PKIXReason.NO_TRUST_ANCHOR) trustFailure = true;
        }
      }
      return target.host() + " " + (target.issuer() != null && trustFailure ? "rejected" : "inconclusive") + " " + error.getClass().getSimpleName();
    }
  }
  public static void main(String[] args) throws Exception {
    System.out.println("java=" + System.getProperty("java.version") + " vendor=" + System.getProperty("java.vendor"));
    List<Target> targets = new ArrayList<>();
    targets.add(new Target("github.com", null));
    targets.add(new Target("pypi.org", null));
    for (String chain : List.of("emudhra", "aaa-emudhra"))
      for (String kind : List.of("dv", "ov", "ev"))
        for (String variant : List.of("g3b", "g3c"))
          targets.add(new Target(kind + "-" + variant + "-" + chain + ".we-have-a-problem.com", "EM " + kind.toUpperCase() + " TLS CA - " + variant.toUpperCase()));
    ExecutorService pool = Executors.newFixedThreadPool(4);
    int exposed = 0, inconclusive = 0;
    try {
      List<Future<String>> futures = new ArrayList<>();
      for (Target target : targets) futures.add(pool.submit(() -> probe(target)));
      for (Future<String> future : futures) {
        String result = future.get();
        System.out.println(result);
        if (result.contains(" exposed")) exposed++;
        if (result.contains(" inconclusive") || result.contains(" canary_changed")) inconclusive++;
      }
    } finally { pool.shutdownNow(); }
    System.exit(exposed > 0 ? 1 : inconclusive > 0 ? 2 : 0);
  }
}
