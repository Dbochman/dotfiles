import java.nio.file.*;
import java.security.*;
import java.security.cert.*;
import java.util.*;

class BuildPublicTrustStore {
  public static void main(String[] args) throws Exception {
    // Public certificates only: this fixed password is not a credential.
    Path source = Path.of(args[0]), destination = Path.of(args[1]);
    KeyStore store = KeyStore.getInstance("PKCS12");
    char[] password = "public-ca-only".toCharArray();
    store.load(null, password);
    try (var input = Files.newInputStream(source)) {
      for (var certificate : CertificateFactory.getInstance("X.509").generateCertificates(input)) {
        X509Certificate x509 = (X509Certificate) certificate;
        if (x509.getBasicConstraints() < 0) throw new IllegalArgumentException("Non-CA certificate");
        String alias = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(x509.getEncoded()));
        store.setCertificateEntry(alias, x509);
      }
    }
    if (store.size() < 100) throw new IllegalStateException("Unexpectedly small public CA bundle");
    try (var output = Files.newOutputStream(destination, StandardOpenOption.CREATE_NEW)) {
      store.store(output, password);
    }
    System.out.println("Public CA certificates imported: " + store.size());
  }
}
