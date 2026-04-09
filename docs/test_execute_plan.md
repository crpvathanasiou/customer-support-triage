Καλύπτει 3 βασικά cases:

retrieval step γεμίζει retrieved_documents
response step γεμίζει response_draft
failure στο response drafting οδηγεί σε failed step + needs_human_review

Τι ελέγχουν αυτά

Το πρώτο test αποδεικνύει ότι ένα retrieval step:

εκτελείται
γεμίζει retrieved_documents
γίνεται completed

Το δεύτερο αποδεικνύει ότι ένα response step:

χρησιμοποιεί το LLM wrapper
γεμίζει response_draft
ενημερώνει metadata

Το τρίτο αποδεικνύει resilience:

αν αποτύχει το drafting
το step σημαίνεται failed
το workflow γυρίζει σε needs_human_review
το human step μένει pending