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
