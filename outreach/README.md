# Letters

Requests sent on the site's behalf, kept as a record. Each starts with its To: and Subject: lines, and is sent from the
charity's Gmail account by Where They Stand's `deploy/mail.sh` (its `docs/HOSTING.md`, "Email from the server"):

    bash /opt/hansardgpt/deploy/mail.sh send /opt/wherecountriesstand/outreach/2026-11/un-general-debate.txt

A letter already sent is skipped. Replies arrive in the account's inbox.

The server's address is never committed: the repository is public, and Cloudflare keeps the server's address
hidden. A letter that needs it says `[the server's IP address, as given in the letter sent]` instead, and the
address is filled in only when sending. Copy the letter outside the repository, keeping its file name (the record
of letters already sent goes by it), put the address in the copy, send the copy, and delete it:

    cp /opt/wherecountriesstand/outreach/2026-11/un-general-debate.txt /root/
    nano /root/un-general-debate.txt
    bash /opt/hansardgpt/deploy/mail.sh send /root/un-general-debate.txt && rm /root/un-general-debate.txt
