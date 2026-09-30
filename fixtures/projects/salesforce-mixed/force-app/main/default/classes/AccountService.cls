public with sharing class AccountService {

    // Positive (bulk): a query and a DML statement inside a loop.
    public static void touchContacts(List<Account> accounts) {
        for (Account acc : accounts) {
            List<Contact> contacts = [SELECT Id, LastName FROM Contact WHERE AccountId = :acc.Id];
            update contacts;
        }
    }

    // Positive (injection): user input concatenated into dynamic SOQL.
    @AuraEnabled(cacheable=true)
    public static List<Account> getAccounts(String nameFilter) {
        String soql = 'SELECT Id, Name FROM Account WHERE Name LIKE \'%' + nameFilter + '%\'';
        return Database.query(soql);
    }

    // Positive (CRUD/FLS): DML without any access check or user mode.
    public static void renameAccount(Id accountId, String newName) {
        Account acc = new Account(Id = accountId, Name = newName);
        update acc;
    }
}
