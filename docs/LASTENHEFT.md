# Lastenheft Bau-System („USB-Stick“)

Dieses Dokument ist die Quelle für das Ziel des Bau-Systems (Skill `to-spawn` und seine Begleit-Skills
`grill-with-docs` → `/to-spec` → `/to-tickets` → `/to-spawn`). Es gilt für **jedes** Repo und **jede**
Maschine (Windows-PC und Linux-Bau-Server), nicht nur für das Repo, in dem es entstanden ist.

Neue Einträge werden unten angehängt (`## L<n>`), alte nie gelöscht. Jeder Eintrag enthält Davids
Wortlaut 1:1 (Tippfehler bleiben stehen) und danach eine Verdichtung in prüfbare Anforderungen.

---

## L1 — Grundauftrag „Specs laufen überall durch“ (David, 2026-11-21)

### Wortlaut (1:1)

> Deine Aufgabe ist das du überwachst und schaust das auf dem bau srever zuverlässig die fixes unserer Dev pipeline komplett gebaut werden und live gehen sodass wir in zukunft reibungsloser auf dem Bau server und auf dem PC an specs arbeiten lassen können ohne ständig aufgehalten zu werden, es wurde schon einges erarebitet aber es gibt konflikte zb beim Repo das die abuarbeiten selber sich hindern daran das sie im selben repo sind wie zb die haupt app selber das und was sonst noch hindert daran das der bau auf dem bau serer zuverlässig unsere dev pipeline optimiert sauber lösen sodass alles end to end fertig wird und wir endlich egal auf welcher maschiene egal in welchem Repo Projekt Specs planen und bauen können ohne sändig an den bekannten oder anderen sachen gehindert zu werden das ist das Ziel!
>
> Wichtig uu verstheen ist, dass:
> Diese arbeiten haben ncihts unbedingt mit unserer App in diesem Repo zu tun sondern gelten für alle Projekte und Repos die wir auf dem bau server laufen lassen wenn irgendwas in deisesn repos technsich oder infrastruktur technisch fehlt soll das automatisch dann für dieses projekt angelegt werden jeweils und dann die selben features erben sodass dort ebenfalls der bau sauber durchläuft eines specs eines anderen Projekts
>
> Die kern aufgabe ist also die Pipeline an sich zu verbessern egal für welchen Spec egal von welchem Projekt klar soll unsere derzeitge app/repo damit genau auch besser laufen (beides muss erreicht werden)
>
> die to spawn skills sollen genau das in ihrer DNA haben und genau in den aspekten verbessert werden sodass man die sauber in anderen Repos verwnden kann bei anderen Specs diese skills oder die Repos der Skills sind genau der "USB-STICK" den man Prjekt unspezifisch oder repo unspezifisch ein und ausstecken kann und der immer gleich läuft und immer für die selben guten ergebnisse sorgen wird das ist die einzige kosntante, denn in anderen Fremden app repos gibt es noch keien bewusstsein oder Code der das sauber nach den standards die wir jetzt etabieren lassen arbeiten kann
>
> also ein einziger Ort zb das skill repo wo das alles als worklfow fest gelegt ist oder als System Checkliste etc für Windows eimal und für linux wie usneren Server
> wenn ich den skill also in einem anderen repo auf einer anderen Macshine aufsetze dann soll der sich die sachen selbst zurcht legen also eien art Setup und das erstmal sichergehen das alle weichen gestellt sind wenn das dann einmal gmacht wurde für das repo soll das immer und zuverlässig durch laufen bei jedem spec den man für deises Repo dann bauen lässt soll das nach diesen standards lauufen, zb wenns noch kein Gateway gibt wie es der Skill braucht dann sorgt der dafür das es das gibt und wenn wir im skill das updaten das wir mit einer neuen art und weise arbeiten was ode Gates angeht also code testing etc dann soll das da auch berücksichtigt werden und immer erstmal das system updaten danach dann weiter sauber an den neuen Specs arbeiten das ist das Ziel
>
> Bisher ist das porblem das es gemischt ist mit dem aktuellen app repo hier in dem wir unse befinden und das es noch ncith klar defineirt ist wo die neuen ädenrungen genau leben sollen das vermischt permamnent den aktuellen repo bau mit dem system bau um den es eignetlich grade geht auf dem Bau server, aber das Repo und die Hindernisse die wir dabie beim spec bau immer hatten sind nur referenzen für unsere Skills welche fehler gemacht wurden und welche Technscihen nachteile wir hatten (zb alte worktrees die speicher fressen) und das bewusstsein darüber zb das die einach gelöscht werden können wenn die arbeite fertig ist und das man da nciht den user fragen muss dafür und das es direkt ein script dafür gibt welches das egal bei wlechelm end gerät (windows doer linux) sauber erledigt. Also das der Skill sein bewusstsein hat nach welcehn SOPs und Kriterien und in welcher Entwiklungsumgebung er midnestens arbeiten MUSS und nach welchen nicht und das der seine Tools hat mir denen er schnell jederzeit arbeiten kann zb scripte sodass der schneller ist wie wenn der alles nochmal neu selebr erstellen muss imer wieder aber dennoch flxebile genung ist keine anpassungen je repo zu machen weil er das oberste Ziel hat das der Spec bau voll autonom auf staging geht. Apropp Staging das wäre zb auch eine sache die geschaut werden muss das es das erstmal gibt denn nicht in jedem Repo gibt es schon eine staging varainete und wenn das zb dem Skill fehlt oder dem SOP dann soll das erstmal erstellt werden mti einem agenten team, darum gehts.
>
> also der Skill gleicht immer den stand der Entwicklungsumgebung ab ob der A überhaupt vorliegt wie benötigt und B noch up to date ist, wenn beides JA dann abreitet er am spec zuverlässig und sauber durch das ist das Ziel.
>
>
>
> Ich wünsche mir das ich in egal welchem APP Repo grill with docs machen kann dann to spec dann to tickets dann to spawn und egal von welchem standpunkt ich das mache ob zum ersten mal oder zum 10ten mal ob gestern und heute oder vor 2 Monaten zuletzt und heute wieder das egal von welchem Standpunkt aus, ich sessions lokal oder auf dem Server spawnen kann und die dann mit ihrem Aufseher komplett bist alles auf staging fertig ist autonom durch arbeiten ohne hindernisse selber etnscheidungen treffen und durchziehen bis der Spec durch ist und das einfach nur loggen und mir diese entscheidungen samt dem fertigen spec auf staging präsenteiren sodass ich entwder in eine neue iterations rrunde geehn kann zum nachbesseern oder direkt live schalten kann. Das Ziel ist einfach das Specs einfach komplett durchgebaut werden und nichts uns hindert. Keine ständigen rückfragen der KI, keine alten Worktrees die den Speicher killen, keine falsch geschlossenen oder geöfnfeten tikets die den weiter bau blockeiren, keine einfach ohne sinn und kommentar setehen gebliebennen sessions die nicht weiter bauen, keine unnnötigen komplizierten wege die dafür sorgen das wir uns städnig aufhängen sondern standarditiserte prozesse die immer funrkioneiren, keine gateway peobleme die dafür sorgen das das ferigg werden auf staging Stunden lang dauert sondern state of the art Gateways die smart nur das kontrollieren was auch kontrolliert werden muss und dabie intelligent mit den RAM ressourcen umgehen sodass auch große Specs sauber durchlaufen können, auch hier einen intelligenten standard wie zb das reisverschluss verfahren auf der AUtobahn das ist auch eine reglung im menschlichen vegleich das verhindert das es staus oder neue unfälle oder aufhänger gibt genausi brauchen wir überall SOPs die sauber laufen und funktionieren egal in wlechem repo egal in welchem Spec egal bei welchem Ticket.
>
> Das ist mein gewünschter Ist zustand. Auf dem Bau server laufen schon sachen entwder nimmst du die unter deine KOntrolle und richtest die richtig aus sodass die dem Ziel dienen oder du beendest die arbeiten und etnscheidest slebst durch neue sub agenten und sub agenten der sub agetnen workflwos wie du selber die zeiele erreichen willst, beides steht dir frei und offen.
>
> beim bau möchte ich aber folgendes ausdrücklich nicht! Ich möchte nicht ständig gefragt werden ob jenes oder anderes okay ist, entschiede selbst teste es meintewegen mit einem kleinen eigenen mini repo oder anderen möglcihketien und mini tests oder suche mit agenten in alten chat verläufen was meine entscheidung zu disem Punktt wohl sein muss und trif die entschidungen selbst, ich möchte nicht ein einziges mal irgned etwas sagen müssen bis der bau fertig ist ich lasse extra den PC an und am internet genauso auch den server an und am internt somit hast du Windows und Linux beides zur verfügung. Ich möchte dich nicht neu starten müssen zb wegen Context window oder anderem sondern du spawnst einfach einen anchfolger selbst der genau gelich das hier alles weis und ausführt und alles von dir erbt also einen 1:1 clon von dir mit frischem context du fragst mich nicht ein einziges mal ob ich händisch eine neue session starte sondern du erledigst das immer selbst das selbe gitl auch für stehen geblieben aufpasser oder worker sessions, wenn irgendwas von dem bisher gebauten dir hilft beim erreichen der ziele dann nutze es wenn nicht dann finde einen anderen weg die oben genannten Ziele und aktzeptanzkriterien zu erreichen. Du hörst erst auf wenn du dir sicher bist das das Ziel erreicht wurde!
>
> logge diesen Promt was ich hier 1:1 geschreiben habe einmal sauber ab als Lastenheft eintrag weil das ist wirklich genau beschrieben einmal was ich mir vorstelle und was ich haben möchte und was nicht und das brauchen wir in zukunft sodass ich nciht alles nochmal neu erklären muss

### Verdichtung in prüfbare Anforderungen (L1)

**Ort und Trennung**

- **A1 Ein Ort:** Alle Regeln, SOPs, Checklisten und Werkzeuge des Bau-Systems leben im Skill-Repo
  (`Shavy72/claude-skill-to-spawn`), nicht im App-Repo. App-Repos enthalten nur ihre eigene,
  vom Skill erzeugte Konfiguration (`.to-spawn/`) und dünne Weiterleitungen.
- **A2 Trennung System ↔ App:** System-Arbeit (Skill, Gates, Hooks, Aufräumen) und App-Arbeit
  (Spec-Code) laufen in getrennten Repos, Bäumen und Freigaben. Kein System-Ticket blockiert
  einen App-Bau und umgekehrt.
- **A3 Referenz statt Kopplung:** Fehler und Erfahrungen aus einem App-Repo fließen als
  allgemeine Regel/Prüfung in den Skill, nie als Sonderfall für dieses Repo.

**Einstecken („USB-Stick“)**

- **A4 Setup/Abgleich vor jedem Spec:** Der Skill prüft beim Start (a) ob die nötige Umgebung im
  Repo und auf der Maschine vorliegt und (b) ob sie dem aktuellen Skill-Stand entspricht. Fehlt
  etwas oder ist es veraltet, richtet er es zuerst ein bzw. bringt es auf Stand, dann baut er.
- **A5 Fehlendes wird angelegt:** Fehlen im Repo z. B. Prüftor (Gate), Tests, Staging oder
  Konfiguration, legt der Skill sie an (bei größeren Lücken wie Staging mit einem Agenten-Team).
- **A6 Updates vererben sich:** Ändert sich im Skill die Arbeitsweise (Gates, Tests, Abläufe),
  ziehen alle eingerichteten Repos beim nächsten Spec automatisch nach.
- **A7 Windows und Linux:** Jede SOP/jedes Werkzeug läuft gleich auf Windows-PC und Linux-Server.
- **A8 Fertige Werkzeuge:** Wiederkehrende Handgriffe (Aufräumen, Worktrees, Prüfen, Ablösen)
  sind fertige Skripte im Skill, keine jedes Mal neu erfundenen Befehle.

**Autonomer Durchlauf**

- **A9 Kette überall:** `grill-with-docs → /to-spec → /to-tickets → /to-spawn` funktioniert in jedem
  Repo, beim ersten wie beim zehnten Mal, nach einem Tag oder nach zwei Monaten Pause,
  lokal oder auf dem Server.
- **A10 Bis Staging ohne David:** Bau-Sessions mit Aufseher arbeiten den Spec bis „alles auf
  Staging“ durch, treffen Entscheidungen selbst, loggen sie, und legen am Ende Spec auf Staging
  + Entscheidungs-Log vor. David entscheidet dann: neue Runde oder live.
- **A11 Keine Rückfragen:** Keine Rückfragen während des Baus. Unklares wird aus Davids früheren
  Aussagen (Gedächtnis, Vault, alte Sessions) abgeleitet oder per Mini-Test entschieden.
- **A12 Selbst ablösen:** Volle Kontexte, stehende Aufseher oder Bau-Sessions werden automatisch
  durch eine frische Nachfolge-Session mit vollem Wissen ersetzt — nie durch David.

**Ausdrücklich nicht (Abnahme-Negativliste)**

- **N1** keine ständigen Rückfragen der KI
- **N2** keine alten Worktrees, die den Speicher füllen — fertige Arbeit wird ohne Rückfrage per
  Skript aufgeräumt, auf Windows und Linux
- **N3** keine falsch geschlossenen oder geöffneten Tickets, die den Weiterbau blockieren
- **N4** keine ohne Grund und Kommentar stehen gebliebenen Sessions
- **N5** keine unnötig komplizierten Wege, die zum Aufhängen führen
- **N6** keine Gate-Probleme, die das Fertigwerden auf Staging stundenlang verzögern — Gates prüfen
  nur, was die Änderung betrifft, und teilen RAM klug ein („Reißverschlussverfahren“: geordnetes
  Einfädeln statt Stau), sodass auch große Specs durchlaufen

**Arbeitsauftrag an die Leit-Session**

- Laufende Arbeiten auf dem Bau-Server entweder übernehmen und ausrichten oder beenden und neu
  aufsetzen — freie Wahl.
- Erst aufhören, wenn das Ziel sicher erreicht ist.

---

## L2 — Nachfolge-Sessions folgen demselben Auftrag (David, 2026-11-21)

### Wortlaut (1:1)

> okay die soll aber dem selben promt folgen und autonom entschidungen treffen usw
>
> und folgende sessions ebenos

Anlass: Die erste Ober-Aufsicht hatte sich selbst durch eine Nachfolge-Session abgelöst.

### Verdichtung in prüfbare Anforderungen (L2)

- **A13 Auftrag vererbt sich 1:1:** Jede Nachfolge-Session — die erste wie die zehnte, Leit-Session,
  Aufseher oder Bau-Session, am PC wie auf dem Server — arbeitet nach demselben Auftrag wie ihre
  Vorgängerin. Der Wortlaut (L1) wird bei jeder Ablösung unverändert mitgegeben, nie als
  Zusammenfassung. Schärft A12.
- **A14 Autonomie vererbt sich mit:** Auch jede Nachfolge-Session entscheidet selbst und fragt
  David nicht (A11, N1). Eine Übergabe, die Wortlaut oder Autonomie-Regel nicht enthält, ist ungültig.

---

## L3 — Tempo: schnelles, kluges Prüfen in jedem Repo (David, 2026-11-21)

### Wortlaut (1:1)

> schau das du auch beim Thema speed lösungen entwickelst die schnell und smart sind zb beim testing oder Gate durchläufen das da smarte und schnelle State of the art workflows genutzt werden und getestet werden die nachweislich gut performen und die für agentische KI Arbeit wie wir sie haben ausgelegt sind und mit dem Code shipping tempo mithalten können das ist mir auch sehr wichtig das wir nicht "bastel" oder schlechte Lösungen nutzen sondern nach dem besten ansatz gehen den man machen kann repo unspezifisch Spec unspezifisch soll immer der smaretes Testing und gate ansatz gefahren werden der individuell am besten und am schnellsten wäre am besten wäre eine art schweizer Taschenmesser welches immer ready ist an möglichkeiten Code zu testen und gates laufen zu lassen und die KI sessions in egal welchem Projekt nutzen immer flexibel das was am besten zum auftrag passt und das was mindestens notwendig ist und machen nichts unnötiges sondern immer nur das was mindestens notwendig ist um stabilen guten Code zu gewährleisten aber auch das rojekt bau tempo niht unnötig zu behindern
>
> Das ist mir auch noch sehr wichtig generell schreib das mal fest das das berücksichtigt udn getestet wird

Anlass: Zwischenstand der Ober-Aufsicht am 21.11.2026, 20:06. Schärft N6 und die Abnahme-Zeile M9.

### Verdichtung in prüfbare Anforderungen (L3)

- **A15 Prüf-Werkzeugkasten („Schweizer Taschenmesser“):** Der Skill bringt einen fertigen Satz an
  Prüf- und Gate-Werkzeugen mit. Er ist in jedem Repo und auf jeder Maschine (Windows-PC,
  Linux-Bau-Server) sofort benutzbar, ohne dass das Repo dafür etwas Eigenes bauen muss.
- **A16 Kleinste ausreichende Prüfung:** Je Auftrag wird nur das geprüft, was mindestens nötig ist,
  um stabilen, guten Code zu sichern. Die Auswahl richtet sich nach der Änderung und ihrem Risiko,
  nicht nach Gewohnheit. Nichts läuft „zur Sicherheit“ doppelt oder ohne Grund.
- **A17 Die Session wählt flexibel:** KI-Sessions in jedem Projekt nehmen aus dem Werkzeugkasten das,
  was zum Auftrag passt. Welche Prüfung lief, welche ausgelassen wurde und warum, steht in einem
  kurzen Prüf-Protokoll je Lauf.
- **A18 Tempo hält mit dem Bauen mit:** Prüfen und Gate sind nie der Engpass. Viele parallele
  Tickets laufen ohne Stau durch (N6). Zeiten je Stufe und Wartezeit werden bei jedem Lauf gemessen.
- **A19 Bester bekannter Ansatz, mit Nachweis:** Gebaut wird nach dem Stand der Technik für
  KI-gestütztes Bauen mit hohem Tempo. Vor dem Bau werden mindestens zwei grundverschiedene Ansätze
  verglichen, mit Quellen und eigenen Messwerten. Kein Eigenbau, wo es ein bewährtes Werkzeug gibt.
- **A20 Unabhängig von Repo und Spec:** Der Werkzeugkasten erkennt selbst, was für ein Projekt vor
  ihm liegt (Sprache, Test-Werkzeug), und enthält kein Wissen über ein bestimmtes Repo.
- **A21 Bewiesen, nicht behauptet:** Tempo und Sicherheit werden gemessen und getestet. Tempo über
  Zeiten vorher/nachher. Sicherheit über absichtlich eingebaute Fehler: Die schnelle Auswahl muss
  jeden Fehler finden, den der volle Lauf findet.

**Ausdrücklich nicht gewollt (L3)**

- **N7** keine Bastel- oder Behelfslösungen beim Prüfen und im Gate
- **N8** keine unnötigen Prüfungen, die das Bau-Tempo bremsen
- **N9** kein Tempo auf Kosten der Stabilität — schneller heißt nie, dass Fehler durchrutschen

---

## L4 — Aufräumen landet nie bei David (David, 2026-11-21)

### Wortlaut (1:1)

> ja was soll denn das mann darum geht es doch warum soll ich irgendwas manuell weider löschen das ist doch genau das worum es geht!!

Im selben Zug in einem zweiten Fenster (Ober-Aufsicht Session 2):

> haben doch extra funktionen die sowas autoamtiscch löschen was soll ich da machen

Anlass: Die Ober-Aufsicht hatte David einen Lösch-Befehl für zwei alte Test-Kopien im `/tmp` des
Bau-Servers hingelegt („geparkt für David“), weil ein Schutz-Hook das Löschen aus der Session
blockte. Genau das ist der Fehler, den L1 (N1, N2) abstellen soll.

### Verdichtung in prüfbare Anforderungen (L4)

- **A22 Aufräumen macht das System selbst:** Reste fertiger Arbeit (Arbeitsordner, Test-Kopien,
  Temp-Ordner, Zweige, Fenster) räumt der Hausmeister oder die Session, die sie findet — auf jeder
  Maschine, auch in `/tmp`. David bekommt dazu nie einen Befehl, eine Frage oder einen Hinweis
  „bitte löschen“.
- **A23 Ein Schutz, der sicheres Aufräumen blockt, ist ein System-Fehler:** Blockt ein Hook oder
  eine Sperre das Löschen von sicher Wegwerfbarem (Ticket zu, kein Prozess, nichts Frisches), wird
  das nicht an David weitergereicht, sondern an der Wurzel behoben: Der Hausmeister bekommt den
  Weg, und der Fall wird als Issue im Skill-Repo gebaut. Bis dahin räumt die Ober-Aufsicht selbst.
- **A24 „Wartet auf David“ nur für echte Entscheidungen:** In Übergaben und Berichten steht unter
  „wartet auf David“ nur, was wirklich nur er entscheiden kann (App-Live, Geld, Konten echter
  Personen). Aufräumen, RAM, Platte, Neustarts gehören nie dorthin.

**Ausdrücklich nicht gewollt (L4)**

- **N10** kein Handgriff für David beim Aufräumen — kein Befehl zum Abtippen, kein „geparkt für David“

---

## L5 — Jederzeit weiterbauen können (David, 2026-11-21)

### Wortlaut (1:1)

> okay alles soll gu dokumenterit werden das jeder zeit falls der bau abgebrochen werden sollte daran weiter gerabeiet werden kann in richtugn des großen Ziels dahinter

Anlass: Zwischenstand 22:16 — der Bau läuft über viele sich ablösende Sessions auf zwei Maschinen;
Übergaben lagen bis dahin teils nur als Datei auf einer Maschine.

### Verdichtung in prüfbare Anforderungen (L5)

- **A25 Ein Einstieg:** Es gibt genau ein Dokument, bei dem man anfängt: `docs/WIEDEREINSTIEG.md`.
  Es nennt Ziel, Fundorte, Stand und die Schritte zum Weiterbauen — verständlich ohne Chat.
- **A26 Gesichert, nicht nur auf einer Maschine:** Auftrag, Plan, Abnahme und jede offene Arbeit
  liegen im Repo (Dokumente, Issues, Pull-Requests, hochgeladene Zweige). Übergaben und
  Entscheidungs-Log beider Maschinen werden von selbst in den privaten Notiz-Tresor gespiegelt.
- **A27 Immer aktuell:** Der Stand wird nach jedem gemergten Paket und bei jeder Ablösung
  fortgeschrieben. Angefangene Arbeit wird mindestens bei jeder Ablösung hochgeladen. Ein Abbruch
  kostet höchstens die Arbeit seit dem letzten Hochladen.
- **A28 Bewiesen:** Eine frische Session ohne Chat und ohne Übergabe-Datei findet allein über den
  Einstieg den richtigen Stand und den richtigen nächsten Schritt (Kaltstart-Probe).

**Ausdrücklich nicht gewollt (L5)**

- **N11** kein Wissen, das nur in einem Chat, im Kopf einer Session oder auf einer einzigen Maschine liegt

---

## L6 — Zwei Werkzeugkästen statt „Windows reparieren“ (David, 2026-11-22)

### Wortlaut (1:1)

> eine sache noch zum system ausbau, mich stört etwas das das was unter Windows noch ncith sauber funktioniert das dass als reparatur angesehen wird das halte ich vom Wording her falsch, es ist doch klar das wenn man dem Windows das gewand von linux anzieht das da was nicht richtig läuft und reparieren heist für ich das man was an dem schon funktionierenden linux Zeug irgendwas ändert.. es ist doch schlauer wenn man für beides ein Werkzeugkasten jeweils hat den die jewiels dort arbeitende KI nehmen kann je nachdem auf welchem system sie arbietet und es läuft autoatmsich sauber und getrennt be beiden systemen das mit dem reparieren implziert für mich das wir uns ewig im kreis drehen würden weil wir was reaprieren für Windows und damit Linux wieder was kaputt machen oder anders gessgt wir den heiligen Gral dazwischen suchen was unnötig kompliziert ist anstatt einfach auf zwei getrennten äasten zu bauen, wo beide eifach das haben was bei beidem funktioniert und das jeweils nur haben was bei sich funktioniert also wenn linux der Nagel ist dann greift die KI zum Hammer und bleibt beim Hammer und wenn Windows die schraube ist dann greift die KI zum schraubendreher und bleibt dabei und wenn siech was tasucht erkennt das die KI jeweils und nutzt autoamtisch das werkeuzeug für die andere umgebung jeweils das würde ich gern so hand haben so wäre es am schlausten und wir doktorn nicht unnötig rum

Auf die Rückfrage, ob gemeint ist „ein gemeinsamer Kern (Ablauf, Regeln, Blocker-Logik), je System ein
eigener Werkzeugkasten (Fenster öffnen, Prozesse starten, geplante Aufgaben, Pfade), der Skill erkennt
das System selbst; ein Windows-Fehler wird im Windows-Kasten gelöst, nie durch Verbiegen des Linux-Wegs“:

> ja genau so linux ist ja schon weit oder? das müsste man ja nicht viel umbauen da das ja an sich schon passt oder?

Zusatz ca. 16:35, zur Auslegung „je System ein tiefes Modul, der Kern ruft nur eine schmale, für
beide Systeme gleiche Schnittstelle auf“:

> genau windows und linux sind eigene module mit schmalen interfaces und tiefer funktionalität darum gehts

Zusatz ca. 21:25, zur Einordnung von Befunden (Grundlage der Labels `system:windows`,
`system:linux`, `system:kern`):

> okay genrell bei der issue ereknnung müssen wir immer identifizeren auch ist das ein windows problem oder einn linux problem oder ein gemeinsmes problem und dann je nachdem das issue anlegen zum fixen sonst drehen wir uns im kreis das passt auch zu dem was ich vorhin gesagt hatte mit den beiden getrennten werkzeug kisten je beetriebssystem

Anlass: Die Arbeit an der Windows-Seite lief bis dahin unter „fix(windows)“ und änderte dabei teils
Dateien, die beide Systeme nutzen.

### Verdichtung in prüfbare Anforderungen (L6)

- **A29 Gemeinsamer Kern:** Ablauf, Regeln und Blocker-Logik gibt es genau einmal. Der Kern fragt
  nie selbst, auf welchem System er läuft.
- **A30 Je System ein Werkzeugkasten:** Alles, was vom System abhängt (Fenster öffnen, Prozesse
  starten und finden, geplante Aufgaben, Pfade, Zeilenenden, Ausgabe-Kodierung), liegt in einem
  Kasten für Linux und einem für Windows hinter derselben schmalen Tür.
- **A31 Automatische Wahl:** Der Skill erkennt das System selbst und nimmt den passenden Kasten.
  Wechselt die Umgebung, wechselt das Werkzeug mit – ohne Einstellung von Hand.
- **A32 Getrennt gebaut:** Arbeit für Windows ändert keine Datei des Linux-Kastens und umgekehrt.
  Ein Prüf-Skript macht den Bau rot, wenn eine System-Abfrage außerhalb der Kästen steht oder ein
  Paket beide Kästen zugleich anfasst.
- **A33 Wording:** Fehlt unter Windows etwas, heißt die Arbeit „Windows-Werkzeug bauen“, nicht
  „reparieren“ oder „Fix“. „Reparieren“ ist nur, was vorher auf demselben System schon lief.
- **A34 Erst zählen, dann umbauen:** Vor dem Umbau steht eine Bestandsaufnahme mit Zahlen (unten).
  Davids Erwartung „Linux muss kaum umgebaut werden“ wird damit belegt oder widerlegt, nicht angenommen.

- **A35 Trennungs-Kontrolle (bedingt):** Zum Vorschlag „Prüf-Skript in der Skill-Suite: Wortsuche außerhalb
  der zwei System-Kästen nach System-Abfragen und nach Werkzeugen, die es nur auf einem System gibt
  (`tmux`, `crontab`, `powershell`, `schtasks`, `wt.exe` …); Treffer = rot mit Datei und Zeile; kurze
  Ausnahme-Liste“ sagte David (2026-11-22):

  > okay wenn uns das eher nützt als hindert dann mit rein

  Das ist ein bedingtes Ja. Die Wortliste kommt aus der Bestandsaufnahme; nach dem Bau werden
  Fehlalarme gezählt. Hindert die Kontrolle mehr als sie nützt (viele Fehlalarme, lange
  Ausnahme-Liste), wird sie wieder ausgebaut. Sie gilt nur für Arbeit am Skill selbst, nicht für
  Spec-Bauten, die den Skill nur benutzen.

**Ausdrücklich nicht gewollt (L6)**

- **N12** kein Windows-Paket, das den funktionierenden Linux-Weg verändert („im Kreis reparieren“)
- **N13** keine Suche nach dem einen Code, der auf beiden Systemen gleich läuft, wo zwei einfache
  getrennte Wege reichen
- **N14** keine zwei komplett getrennten Skill-Fassungen – der Kern bleibt einer

### Bestandsaufnahme (Stand main 79d2fad, gezählt 2026-11-22, grob per Textsuche)

- Direkte System-Abfragen (`sys.platform`, `os.name`, `platform.system()`) außerhalb der Tests:
  **50 Stellen in 25 Dateien**. Die meisten in `to_spawn/hausmeister.py` (6), `startklar.py` (5),
  `umzug.py` (4), `hausmeister_aufgabe.py` (4), je 3 in `waechter_takt.py`, `starter.py`,
  `spawn.py`, `config.py`. Die Weichen liegen also heute verstreut, nicht hinter einer Tür.
- Ansätze für Kästen gibt es schon: `to_spawn/hausmeister_windows.py`, `to_spawn/prozessbaum.py`.
- Tests: 63 Test-Dateien enthalten eine System-Abfrage oder ein `skipif`.
- Was die bisherigen Windows-Pakete außerhalb von Tests geändert haben:
  233 → `hausmeister_windows.py`, `prozessbaum.py` · 240 → nur Tests · 256 → `konsole.py`,
  `sperre.py`, `uebergabe_sichern.py` (gemeinsam) · 265 → `pruef/regeln.py` (gemeinsam, 1 Zeile) ·
  255 (offen) → `aufpasser.py` (gemeinsam, läuft auf Linux im Cron) und `prozessbaum.py`.
- Noch nicht gemessen: wie viele der 50 Stellen den Linux-Weg beim Herausziehen wirklich berühren.
  Gebaut wird erst nach der Probe und nach Davids Wort.

---

## L7 — Ressourcen-Regeln: messen, drosseln, nie stehen bleiben (David, 2026-11-22)

### Wortlaut (1:1)

> … wegen dem Ram warum läuft der den voll was verbraucht den da die mieste perfomrance wir haben schon viel an dem gate rumgebastelt und viel versucht und untersucht das ist auch eins der sachen die auf jedenfall systematisiert werden sollten mit einer sauberen SOP oder einem fleblen werkzeugkasten aus verschenednen SOPS

> ja es soll da gewisse standards geben je nach hardware was verfügabr ist das soll halt berücksichtigt werden und klaren regeln folgen das man immer den sweetspot anpeilt zwischen performance speed und gründlichkeit beim jeweiligen Testgate

> wir brauchen klare regelungen und AUgen auf der Umgebung was die ressourcen angeht und klare regeln ab wann was gilt am besten so das es am mesiten sinn macht

Zum Geräte-Logbuch:

> ja dazu ein md file wo gelogt wird was pro gerät und umgebung gemessen ist etc so kann die KI das auch berücksichtigen und sich automatisch anpassen egal ob man auf größere Server skaliert oder auf ienmal auf einem schlechteren laptop arbeiten muss es wird erkannt auf welhem gerät man sich befindet und für jedes Gerät einzeln gelogt und auf dei Daten bei bedarf durch agents zurück gegriffen das müssen reichhaltige logs sein über die hardware und hardware perfomrance und last in vrerschiedenen fällen die laufen

Zum dynamischen Drosseln („Tankanzeige“):

> okay top genau und daher weis er immmer oder der agnt weis daddurch immer wie viele paralel sessions und kann damit auch umgehen mehr oder weniger gleiczitg laufen zu lassen ohne den bau zu sperren als dynamisch "blocken" und autoamtsich dynamsich "entblocken" laufender tickets in agents aber auch als daten zsuatz wann er welchen Code Test Gate laufen lässt zb wenn schon ein gate läuft und ein anderes Ticket in der schalnge steht schaut er erst recht wie er desen gateway optimeirt laufen lassen kann oder welches vorgefertite Gateway SOP er nutzen kann oder sollte um den Bau bestmöglich vorran zu treiben
>
> ich vergelihe es mit der Tank anzeige in einem auto oder reifendruck anzeiger, man drosselt die geschiendigkeit genauso das man weiter fahren kann so das man nciht crasht und stehen bleibt aber trotzdem so schnell es geht das ziel erreicht mit fokus aber das das auto weiter genutzt werden kann weil auto ist immer shneller als "zu Fuss" gehen was das equivalent wäre zu "mensch muss eingreiffen in den spec bau

Anlass: Während der Probe am echten Fall lief der Arbeitsspeicher des Bau-Servers mehrfach hoch, und
die Prüfung je Ticket dauerte rund 11 bis 21 Minuten.

### Verdichtung in prüfbare Anforderungen (L7)

Die Verdichtung ist eine Auslegung der Sessions. David hat den Wortlaut gesagt, die einzelnen Punkte
aber nicht einzeln bestätigt.

- **A36 Gerät erkennen und messen:** Der Skill erkennt selbst, auf welchem Gerät er läuft, und misst
  dort Arbeitsspeicher, Kerne, Platte und die Last typischer Fälle (Prüflauf je Arbeiter-Zahl,
  mehrere Bau-Sessions, Browser-Tests, mehrere Specs gleichzeitig).
- **A37 Geräte-Logbuch:** Je Gerät gibt es ein eigenes, lesbares Logbuch mit Datum je Messung.
  Agenten lesen es bei Bedarf. Ein unbekanntes Gerät wird zuerst gemessen und startet vorsichtig.
- **A38 Feste Stufen an einem Ort:** Aus Logbuch und aktueller Last folgt nach klaren Schwellen, wie
  viele Tickets gleichzeitig bauen, wie viele Arbeiter die Prüfung nutzt und welche Prüf-Tiefe
  läuft. Die Schwellen stehen an genau einer Stelle im Skill, nicht je Repo von Hand.
- **A39 Drosseln statt stehen bleiben:** Wird es eng, hält der Skill Tickets zurück; wird Luft frei,
  lässt er sie von selbst wieder los. Der Bau wird dabei nie gesperrt und braucht keinen Menschen.
- **A40 Prüfung passt sich an:** Läuft schon eine Prüfung und ein weiteres Ticket steht an, wählt
  der Skill die passende vorgefertigte Prüf-Anleitung (Arbeiter-Zahl, Tiefe, Reihenfolge oder
  Bündelung). Baut auf L3 auf.
- **A41 Rangfolge:** Erstens bleibt der Bau nie stehen und stürzt nicht ab. Zweitens ist er
  innerhalb dessen so schnell wie möglich.
- **A42 Erst messen:** Vor jedem Regel-Entwurf stehen Zahlen: Speicher- und Zeit-Spitze eines
  Prüflaufs je Arbeiter-Zahl, Dauer je Ticket gegen Zahl gleichzeitiger Sessions, Speicher je
  Bau-Session mit und ohne Browser-Helfer.

**Ausdrücklich nicht gewollt (L7)**

- **N15** kein Stillstand und kein Absturz, weil niemand auf die Ressourcen geschaut hat
- **N16** keine Handregel je Repo oder je Session für Parallel-Zahl und Prüf-Tiefe
- **N17** keine Geräte-Logbücher im öffentlichen Skill-Repo (sie bleiben lokal oder privat)

**Offen (nicht entschieden)**

- Wie verträgt sich das Zurückhalten bereits laufender Sessions mit ihrem Kontext (anhalten und
  fortsetzen gegen „nur keine neuen starten“)? Mindestens zwei Varianten vergleichen.
- Technik des Logbuchs: Roh-Messwerte maschinenlesbar und die lesbare Datei daraus erzeugt?
- Ob es eine feste Grenze für gleichzeitig bauende Tickets je Spec geben soll, war nur besprochen.

---

## L8 — Bau-Blocker entscheidet der Aufseher, nicht David (David, 2026-11-23)

### Wortlaut (1:1)

Ca. 01:15, als eine Bau-Session ihm eine Ja/Nein-Frage zu Test-Daten auf Staging stellte:

> ist doch auch gegen meinen wunsch das erstmal alles gebaut wird und selbst entschieden wird und erst dann geklärt wird wenn der spec auf staging fertig ist

Ca. 01:50, nachdem die Session ein weitergereichtes „Ja“ abgelehnt hatte:

> okay aber das nervt doch genau das sind doch bau blocker eigetnlcih ist das der job des aufsehers für mich bei sowas zu entscheiden auf grundlage von meinen aussagen was mir wichtig ist aus den gnazen chat protokollen da soll er ein team lossenden die suchen anch meiner meinung in sonem fall und dann entscheiden für mich

Ca. 02:40, nachdem die Session auch die Entscheidung des Aufsehers nicht angenommen hatte:

> was ist das mit meinem Ja das darf nicht sein das ist ausdrücklich von mir autorisiert und in zukunft darf sowas auch nicht sein

Anlass: In der Probe am echten Fall (22./23.11.2026) fragte eine Bau-Session David, ob sie auf
Staging Test-Zuordnungen lösen darf (mit Sicherung, eigener Vorschlag „Ja“). Ein „Ja“ per Nachricht
vom Beobachter und danach vom Aufseher nahm sie nicht an, weil Nachrichten anderer Sessions für sie
nicht als Freigabe des Nutzers gelten. Zwei Tickets standen darüber mehrere Stunden. Erst ein
Neustart mit der Freigabe im Start-Auftrag wirkte. Umsetzung: Issue 278.

### Verdichtung in prüfbare Anforderungen (L8)

Die Verdichtung ist eine Auslegung der Sessions. David hat den Wortlaut gesagt, die einzelnen Punkte
aber nicht einzeln bestätigt.

- **A43 Erst bauen, dann klären:** Eine Bau-Session entscheidet während des Baus selbst und
  sammelt offene Punkte. Geklärt wird mit David erst, wenn die Spec auf Staging fertig ist.
- **A44 Rückfrage ist ein Bau-Blocker:** Kann eine Bau-Session etwas nicht selbst entscheiden, geht
  die Frage an den Aufseher, nie an David.
- **A45 Aufseher sucht Davids Haltung:** Der Aufseher schickt Helfer los, die Davids Aussagen dazu
  suchen (Gedächtnis, Übergaben, Lastenheft, Chat-Protokolle). Er entscheidet auf dieser Grundlage
  und schreibt Entscheidung und Belege ins Ticket.
- **A46 Die Entscheidung kommt an:** Die Bau-Session nimmt die Entscheidung des Aufsehers an und
  arbeitet weiter – ohne Neustart und ohne dass ein Mensch etwas eintippt. Die Befugnis dafür steht
  schon im Start-Auftrag der Session, denn nur der gilt für sie als Wort des Nutzers.
- **A47 Was autorisiert ist, wird nicht gefragt:** Alles auf Staging und an Test-Daten (mit
  Sicherung), im eigenen Arbeitsordner und auf eigenen Zweigen entscheidet die Bau-Session selbst
  und hält es fest.
- **A48 Liste für die Gesamtabnahme:** Alle so getroffenen Entscheidungen stehen je Spec in einer
  Liste (wer, was, warum, Beleg), die David bei der Abnahme auf Staging sieht.
- **A49 Grenze:** Live-System, Kosten und Konten echter Personen bleiben bei David. Dort hält die
  Session an, und auch der Aufseher entscheidet nicht.

**Ausdrücklich nicht gewollt (L8)**

- **N18** keine Rückfrage an David zu etwas, das er schon autorisiert hat
- **N19** kein Stillstand eines Tickets, weil eine Entscheidung die Bau-Session nicht erreicht
- **N20** keine Entscheidung des Aufsehers ohne Belege aus Davids eigenen Aussagen

**Offen (nicht entschieden)**

- Ort und Form des Entscheidungs-Briefkastens (Datei im Arbeitsordner gegen Ticket-Kommentar):
  zwei Varianten vergleichen, siehe Issue 278.
- Was der Aufseher tut, wenn die Helfer keine Aussage von David zu dem Fall finden.

---

## L9 — Abnahme auf Staging: Rundschau, KI-Selbstprüfung, Entscheidungs-Liste (David, 2026-11-23)

### Wortlaut (1:1)

Ca. 15:30, in einer [PC-Session] nach der Probe am echten Fall (Spec 1330):

> ja dann gehört das auch mit in den system bau brauche das rundschau artefakt mit der präsentation der neue features damit ich die mir anschauen kann und damit die KI seber nochmal checken kann ob alles passt und dan kommen dann auch die Punkte rein die im Bau für mich entschieden wurden in einer grobe einfachen liste sodass ich da nachfragen kann und wenn dann alles passt gehts live und wenn noch paar sachen nachbearetei werden müssen dann folge tickets eine weitere iteration
>
> so muss das ablaufen
>
> wir brauchen über dem Aufpasser eine weitere Session die Läuft aber nur bei beadrf einschreitet und maximal token effizient Arbeitet also entweder ohne loop die läuft einfach und wird getriggert wenn sie gebaucht wird oder wwenn sie looped dann sehr token effizient
>
> es geht drum das wirklich entschiedungen für much getrofefn werden und geschaut wird in Obsidian oder alten session logs was meine meinung sein wird etc dann enstsciednen wird
>
> oder sagst du brauchen wir nicht wir geben das einfach zum start auftrag mit das hat deises mal geholfen oder was sagst du oder sagst du das soll der aufseher auch machen selber einfach was wäre da sinnvoll wie gehen wir da vor
>
> Ziel ist ja wirklich das nach plan komplett auf staging gebaut wird und wenn dan die entschidungen angeziegt werden in dem Rundschau dokument oder der seite halt dann sehe ich ja was passier tund entschieden wurde da es ja staging ist ist es ja nicht schlimm und es passiert ja nichts

Anlass: In der Probe am echten Fall (Spec 1330) war die Abschluss-Rundschau zu dünn (7 Seiten,
keine Bilder, keine Klick-Schritte); eine brauchbare entstand erst auf Davids Nachfrage von Hand.
Erst eine Lese-Prüfung auf Davids Frage „kann das live?“ fand drei Lücken vor Live (App-Tickets
1555, 1556, 1557): entschiedenes Verhalten nicht gebaut, keine Sicherung und kein Rückweg für
Live-Daten, Abnahme-Belege fehlten mangels Beleg-Konto. Die Test-Übersicht im Abschluss-Paket
nannte veraltet „0 von 40“.

Ersetzt für das öffentliche Repo: der Name der Chat-Sitzung durch [PC-Session].

### Bestätigter Vorschlag (David: „ja klar“, 2026-11-23 ca. 16:00)

Die [PC-Session] hat auf Davids Frage („was wäre da sinnvoll“) diesen Vorschlag gemacht, David hat
ihn mit „ja klar“ bestätigt:

- Keine eigene Dauer-Session über dem Aufseher. Der Aufseher entscheidet; je offener Frage startet
  er einen frischen Helfer, der in Obsidian und alten Session-Logs Davids Meinung sucht und
  Entscheidung + Fundstelle zurückgibt. Ohne Frage kostet das nichts.
- Jede Entscheidung als eine Zeile in eine Datei je Spec (was · warum · Quelle). Die Rundschau
  zeigt diese Liste automatisch.
- Grenze bleibt: Live, Kosten, Konten echter Personen entscheidet David.
- Ablauf: 1. alles auf Staging bauen · 2. Rundschau automatisch (Bilder, Klick-Schritte,
  Entscheidungs-Liste) · 3. eine KI klickt die Rundschau selbst gegen Staging durch · 4. David
  schaut und fragt nach · 5. passt → live, sonst Folge-Tickets und weitere Runde.

### Klarstellung (1:1, ca. 16:20)

> was meinst du wir reden nicht von der gelchen sache ich rede davon das wärend eines spec baus immer wirder tickets stehen bleiben und was von mir wollen obwohl klar ist das es einfach gemacht werden soll ohne mich darum gehts

Einordnung der [PC-Session]: Kern des Entscheider-Wunsches ist nicht die Abnahme, sondern dass
Bau-Sessions mitten im Spec-Bau stehen bleiben und auf David warten. Was in der Probe half:
Davids Entscheidungen fest im Start-Auftrag der Bau-Session (6–9 Punkte) – danach kam keine Frage
mehr an David.

Anlass (Probe Spec 1330, Nacht 22./23.11., App-Tickets): Löschfrage in 1476, 1479 und 1484 warten
trotz erledigter Blocker, `/implement` startet nicht selbst, 1483 „Nichts zu tun“ nach rotem Gate,
1484 still nach „Review gelb“, 1485 wartet auf eine Live-Freigabe-Frage.

### Entscheidung (1:1, ca. 16:35)

> ja dann ist das erstmal die lösung das man des fest in den ticket start uaftrag mit giebt wie auch fest in den aufseher integriert
>
> okay dann haben wir doch ertsmal genug stoff gesammelt sodass wir dann asap den test spec mal laufen lassen können ist das alles schon itegriert?

Einordnung der [PC-Session]: Die Entscheidungs-Linie gehört fest in den Start-Auftrag jeder
Bau-Session und in den Aufseher-Auftrag, nicht mehr von Hand per BAU_AUFTRAG. Außerdem soll die
Test-Spec so bald wie möglich laufen. Der Start der Test-Spec bleibt an Davids ausdrückliches Wort
gebunden.

Die Linie (Kurzfassung, sinngemäß):

1. Blocker erledigt = geschlossen ODER `staging:fertig` + Commit auf dem Hauptzweig.
2. Dann selbst claimen und `implement` starten.
3. Keine Fragen an David, Vorschlag nehmen, ins Bau-Log, weiterbauen; sonst entscheidet der Aufseher.
4. Staging und Test-Daten frei, auch Löschen, mit Sicherung + Beleg.
5. Nie ohne David: Live, Live-Daten, Live-Freigabe, Kosten, Konten echter Personen – Schritt
   auslassen, Kommentar, Rest weiterbauen.
6. Fertig = grüne Schlange + echter Klickweg auf Staging + Beleg.
7. Vor jedem Push Vor-Prüfungen lokal grün.
8. Jede Entscheidung für David als eine Zeile (was · warum · Quelle).

### Verdichtung in prüfbare Anforderungen (L9)

Die Verdichtung ist eine Auslegung der Sessions. David hat den Wortlaut gesagt und den Vorschlag
oben bestätigt, die einzelnen Punkte aber nicht einzeln. L9 baut auf L8 (A43–A49) auf.

- **A50 Abnahme-Ablauf in fünf Schritten:** Jede Spec läuft so: ganz auf Staging bauen →
  Rundschau entsteht von selbst → eine KI prüft die Rundschau gegen Staging → David schaut und
  fragt nach → passt es, geht die Spec live; sonst Folge-Tickets und eine weitere Runde.
- **A51 Rundschau zeigt die neuen Funktionen:** Die Rundschau zur Abnahme entsteht ohne Nachfrage
  und zeigt je neuer Funktion mindestens ein Bild aus Staging und die Klick-Schritte, mit denen
  David sie selbst findet. Eine Rundschau ohne Bilder oder ohne Klick-Schritte gilt als nicht
  fertig.
- **A52 Entscheidungs-Liste in der Rundschau:** Alle im Bau für David getroffenen Entscheidungen
  stehen je Spec in einer Datei, je Entscheidung eine Zeile (was · warum · Quelle). Die Rundschau
  zeigt diese Liste von selbst, grob und einfach, sodass David zu jeder Zeile nachfragen kann.
- **A53 KI-Selbstprüfung:** Bevor David die Rundschau bekommt, klickt eine frische KI die
  Klick-Schritte der Rundschau selbst gegen Staging durch und hält je Schritt fest: passt / passt
  nicht, mit Beleg. Was nicht passt, wird vor der Abnahme repariert oder in der Rundschau offen
  genannt.
- **A54 Folge-Runde statt Flicken:** Muss nach Davids Durchsicht etwas nachgebessert werden,
  entstehen Folge-Tickets an derselben Spec. Danach läuft der Ablauf aus A50 noch einmal (neue
  Rundschau, neue Selbstprüfung).
- **A55 Kein Dauer-Wächter über dem Aufseher:** Über dem Aufseher läuft keine weitere ständige
  Session. Der Aufseher startet je offener Frage einen frischen Helfer, der in Davids Notizen
  (Obsidian) und alten Session-Logs nach seiner Meinung sucht und Entscheidung + Fundstelle
  zurückgibt. Ohne offene Frage kostet das keine Token.
- **A56 Grenze (wie A49):** Live-System, Kosten und Konten echter Personen entscheidet David –
  weder Aufseher noch Helfer.
- **A57 „Bereit für Live?“ vor der Abnahme:** Bevor eine Spec als „bereit zur Abnahme“ gemeldet
  wird, prüft das System fest: Ist jedes entschiedene Verhalten gebaut? Gibt es Sicherung und
  Rückweg für Live-Daten? Liegen die Abnahme-Belege vor? (Aus dem Anlass; als Vorschlag in
  derselben Notiz festgehalten, nicht als eigener Punkt von David bestätigt.)
- **A58 Zahlen im Abschluss-Paket sind frisch:** Test-Übersicht und andere Zählungen im
  Abschluss-Paket zeigen den Stand zum Zeitpunkt der Abnahme, nie einen alten.
- **A59 Entscheidungen im Start-Auftrag (Klarstellung):** Jede Bau-Session bekommt als Standard
  einen Auftragstext mit den schon getroffenen Entscheidungen des Auftraggebers zur Spec mit. Eine
  Bau-Session ohne diesen Teil im Start-Auftrag gilt als falsch gestartet.
- **A60 Steh-Erkennung löst den Entscheider-Helfer aus (Klarstellung):** Das System erkennt
  maschinell, dass eine Bau-Session mit einer Frage steht oder still ist, und startet damit den
  Helfer aus A55 – ohne Dauer-Schleife. Ohne stehende Session läuft kein Helfer.
- **A61 Entscheidung landet in der Liste (Klarstellung):** Jede so getroffene Entscheidung steht
  mit Quelle als Zeile in der Entscheidungs-Liste der Spec aus A52 und damit in der Rundschau.

Aus der Entscheidung 16:35 (Auslegung der Sessions, Wortlaut oben):

- **A62 Entscheidungs-Linie fest im Start-Auftrag:** Die acht Punkte der Linie (siehe Entscheidung
  16:35) stehen fest im Start-Auftrag jeder Bau-Session, nicht mehr von Hand per BAU_AUFTRAG.
- **A63 Entscheidungs-Linie fest im Aufseher-Auftrag:** Dieselben acht Punkte stehen fest im
  Aufseher-Auftrag. Start-Auftrag (A62) und Aufseher-Auftrag (A63) beziehen die Linie aus einer
  gemeinsamen Quelle (Skill-Issue 376).
- **A64 Test-Spec so bald wie möglich:** Pflicht davor sind die Skill-Issues 273, 274, 282, 283,
  376, 364 und 372. Danach, nicht blockierend: 365–370. Start der Test-Spec weiter nur auf Davids
  ausdrückliches Wort.

**Ausdrücklich nicht gewollt (L9)**

- **N21** keine eigene Dauer-Session über dem Aufseher, die ohne offene Frage Token verbraucht
- **N22** keine Rundschau zur Abnahme, die erst auf Davids Nachfrage brauchbar wird
- **N23** keine Spec live vor Davids Durchsicht auf Staging

**Offen (nicht entschieden)**

- Wie die KI-Selbstprüfung Staging bedient (echter Browser oder Kopflos-Browser) und wo ihr
  Ergebnis steht.
- Ob die Entscheidungs-Datei je Spec die bestehende Übersicht `docs/agents/entscheidungen_<S>.md`
  ist oder aus dem Entscheidungs-Briefkasten (Issue 278) neu erzeugt wird.
