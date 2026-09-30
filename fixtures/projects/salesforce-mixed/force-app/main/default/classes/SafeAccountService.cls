public with sharing class SafeAccountService {

    // Negative: one bulk query with a bind variable, user-mode DML outside the loop.
    public static void touchContacts(List<Account> accounts) {
        Set<Id> ids = new Map<Id, Account>(accounts).keySet();
        List<Contact> contacts = [
            SELECT Id, LastName FROM Contact WHERE AccountId IN :ids WITH USER_MODE LIMIT 2000
        ];
        for (Contact c : contacts) {
            c.LastName = c.LastName.trim();
        }
        Database.update(contacts, AccessLevel.USER_MODE);
    }
}
